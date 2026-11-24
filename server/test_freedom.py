"""Offline test of the Freedom Championship (freedom.py): matchmaking against another account, score
and training matches as live matches, give-up, records, attempts, chests, line-ups.
Season 104 is forced (it opens 2026-11-25). Temporary accounts, no port.
  python test_freedom.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

os.environ["HFH_FREEDOM_SEASON"] = "104"
import accounts  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="hfh-freedom-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import freedom  # noqa: E402
import wire  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


async def dispatch(c, msg_id, pb=b""):
    await server.MESSAGE_HANDLERS[msg_id](c, pb)


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def play(c, pattern):
    await dispatch(c, freedom.MSG_MATCH, P.FightFreedomMatchC2S(pattern=pattern).SerializeToString())
    m = P.FightFreedomMatchS2C.FromString(c.last(freedom.MSG_MATCH))
    assert m.err == 0 and m.data.team.pvp_formation.players, m
    args = P.PVPRoomArgs(pvp_type=P.FIGHT_FIGHTFREEDOM, pvp_args=[0])
    await server.handle_pvp_create(c, P.PVPCreateC2S(args=args).SerializeToString())
    room = P.PVPCreateS2C.FromString(c.last(0x0302)).info
    assert room.away.gamer_id == m.data.enemy_id
    live, n = c.room.live, 0
    while live and not live.over and n < 300:
        await server.handle_match_data(c, b"")
        n += 1
    await server.handle_pvp_result(c, b"")
    br = P.PVPResultS2C.FromString(c.last(0x0304)).battle_result
    return m.data, br


async def main():
    a, b = await login(9301), await login(9302)
    a = await login(9301)
    info = P.FightFreedomInfoS2C.FromString(a.last(freedom.MSG_INFO))
    print("season", info.season.season_id, "score", info.season.rank.score, "attempts",
          info.season.challenge.challenge_times, "/", info.season.challenge.challenge_times_max,
          "defence", sum(1 for i in info.formations.inst_ids if i), "attack", sum(1 for i in info.attack_formations.inst_ids if i))
    assert info.season.season_id == 104 and info.season.rank.score == 1000
    assert any(m == freedom.MSG_TEAM_INFO for m, _ in a.sent)

    # b saves a defence (first 7 of its attack line-up)
    bf = freedom.formation(9302, b.account, "attack")
    req = P.FightFreedomPlayerLineupSaveC2S(module=1, is_attack=False)
    req.formation.inst_ids.extend(list(bf.inst_ids))
    await dispatch(b, freedom.MSG_LINEUP_SAVE, req.SerializeToString())
    assert P.FightFreedomPlayerLineupSaveS2C.FromString(b.last(freedom.MSG_LINEUP_SAVE)).is_attack is False

    real = None
    for _ in range(12):  # the opponent is a real player most of the time
        data, br = await play(a, freedom.SCORE) if real is None else (None, None)
        if data.enemy_id == 9302:
            real = (data, br)
            break
    assert real, "never matched the other player"
    data, br = real
    hs, aw = br.player_grade.home.fight_freedom_score, br.player_grade.away.fight_freedom_score
    print(f"score match vs {data.enemy_name}: {br.result.home.bo_statistics[0].score}-"
          f"{br.result.away.bo_statistics[0].score}, me {hs.old_score}->{hs.new_score}, enemy {aw.old_score}->{aw.new_score}")
    upd = P.FightFreedomUpdateS2C.FromString(a.last(freedom.MSG_UPDATE))
    assert upd.rank.score == hs.new_score and upd.challenge.challenge_times >= 1
    assert freedom.entry(9302)["records"][0]["type"] in (P.FIGHTFREEDOM_DEFENSE_WIN, P.FIGHTFREEDOM_DEFENSE_LOSE)

    before = freedom.entry(9301)["score"]
    times = freedom.entry(9301)["times"]
    data, br = await play(a, freedom.TRAIN)
    assert freedom.entry(9301)["score"] == before and freedom.entry(9301)["times"] == times
    assert not br.player_grade.home.HasField("fight_freedom_score")

    # give up on the start screen
    await dispatch(a, freedom.MSG_MATCH, P.FightFreedomMatchC2S(pattern=1).SerializeToString())
    a.room = None
    await dispatch(a, freedom.MSG_CLOSE, P.PVPCloseC2S().SerializeToString())
    assert freedom.entry(9301)["match"] is None

    await dispatch(a, freedom.MSG_RECORD)
    rec = P.FightFreedomRecordS2C.FromString(a.last(freedom.MSG_RECORD))
    print("records", [(P.FIGHTFREEDOM_RECORD_TYPE.Name(r.record_type), r.enemy_name, r.own_change_score) for r in rec.records])
    assert rec.records

    a.account.set_currency(freedom.BUY_COST_ITEM, 100)
    await dispatch(a, freedom.MSG_BUY, P.FightFreedomChallengeBuyC2S(num=1).SerializeToString())
    assert P.FightFreedomChallengeBuyS2C.FromString(a.last(freedom.MSG_BUY)).buy_times == 1

    # daily match chest: 5 score matches needed for the first one; index / bit = list position
    await dispatch(a, freedom.MSG_REWARD, P.FightFreedomRewardC2S(index=0).SerializeToString())
    assert P.FightFreedomRewardS2C.FromString(a.last(freedom.MSG_REWARD)).err != 0
    freedom.entry(9301)["times"] = 5
    await dispatch(a, freedom.MSG_REWARD, P.FightFreedomRewardC2S(index=1).SerializeToString())
    assert P.FightFreedomRewardS2C.FromString(a.last(freedom.MSG_REWARD)).err != 0  # needs 10
    await dispatch(a, freedom.MSG_REWARD, P.FightFreedomRewardC2S(index=0).SerializeToString())
    assert P.FightFreedomRewardS2C.FromString(a.last(freedom.MSG_REWARD)).challenge_reward == 1 << 0
    await dispatch(a, freedom.MSG_REWARD, P.FightFreedomRewardC2S(index=0).SerializeToString())
    assert P.FightFreedomRewardS2C.FromString(a.last(freedom.MSG_REWARD)).err != 0  # only once

    freedom.entry(9301)["history"] = 1700
    await dispatch(a, freedom.MSG_GIFT, P.FightFreedomGiftC2S(gift_index=4).SerializeToString())
    assert list(P.FightFreedomGiftS2C.FromString(a.last(freedom.MSG_GIFT)).season_gift_got) == [4]

    await dispatch(a, freedom.MSG_TEAM_SAVE, P.TeamFreedomFormationSaveC2S(
        formation=P.TeamFormation(lineup=1, inst_ids=list(bf.inst_ids)[:3]), module=1).SerializeToString())
    assert P.TeamFreedomInfoS2C.FromString(freedom.team_info(9301, a.account)).formations[1].inst_ids

    os.environ["HFH_FREEDOM_SEASON"] = "0"  # closed season: no matchmaking
    await dispatch(a, freedom.MSG_MATCH, P.FightFreedomMatchC2S(pattern=1).SerializeToString())
    assert P.FightFreedomMatchS2C.FromString(a.last(freedom.MSG_MATCH)).err != 0
    print("OK", TMP)


asyncio.run(main())
