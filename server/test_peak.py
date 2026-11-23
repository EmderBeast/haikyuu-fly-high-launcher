"""Offline test of Triple Clash (peak.py) with fake clients: series as quick battles and as live
matches, formations, records, attempts, chests. Temporary accounts folder, no port.
  python test_peak.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-peak-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import peak  # noqa: E402
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


def info(c) -> P.PeakInfoS2C:
    return P.PeakInfoS2C.FromString(c.last(peak.MSG_INFO))


def client_has_next(scores: list) -> bool:
    """PeakBattleUtils.HasNextStage."""
    t = len(scores) - 1
    if t == 1:
        (h0, g0), (h1, g1) = scores
        if (h0 > g0 and h1 > g1) or (h0 < g0 and h1 < g1):
            return False
    return 0 <= t + 1 < 3


async def quick_series(c, index):
    scores = []
    for t in range(3):
        args = P.PVPRoomArgs(pvp_type=P.FIGHT_PEAK, pvp_args=[index, t])
        await dispatch(c, 0x0306, P.PVPQuickBattleC2S(args=args).SerializeToString())
        br = P.PVPQuickBattleS2C.FromString(c.last(0x0306)).battle_result
        scores.append((br.result.home.bo_statistics[0].score, br.result.away.bo_statistics[0].score))
        if not client_has_next(scores):
            return br, scores
    raise AssertionError("series did not end")


async def live_series(c, index):
    scores = []
    for t in range(3):
        args = P.PVPRoomArgs(pvp_type=P.FIGHT_PEAK, pvp_args=[index, t])
        await server.handle_pvp_create(c, P.PVPCreateC2S(args=args).SerializeToString())
        room = P.PVPCreateS2C.FromString(c.last(0x0302)).info
        assert room.home.formation.players[0].info.conf_id and room.away.formation.players[0].info.conf_id
        m = c.room
        n = 0
        while m.live and not m.live.over and n < 200:
            await server.handle_match_data(c, b"")
            n += 1
        await server.handle_pvp_result(c, b"")
        br = P.PVPResultS2C.FromString(c.last(0x0304)).battle_result
        scores.append((br.result.home.bo_statistics[0].score, br.result.away.bo_statistics[0].score))
        if not client_has_next(scores):
            return br, scores
    raise AssertionError("series did not end")


async def main():
    a, b, d = await login(9101), await login(9102), await login(9103)
    a = await login(9101)
    i = info(a)
    print("season", i.season.season_id, "score", i.rank_data.score, "attack teams", len(i.attack.team),
          list(i.attack.power), "defend", list(i.defend.power))
    assert len(i.attack.team) == 3 and len(i.defend.team) == 3
    assert all(len(t.pvp_formation.players) == 13 for t in i.attack.team)
    lst = [(p.gid, p.name, p.score, list(p.power), p.add_score) for p in i.challenge.challenge_list]
    print("opponents", lst)
    assert {p.gid for p in i.challenge.challenge_list if p.gid > 0} == {9102, 9103}
    real = next(k for k, p in enumerate(i.challenge.challenge_list) if p.gid > 0)
    enemy = i.challenge.challenge_list[real].gid

    # save a defence: swap team 1 and 3
    teams = [list(t.pvp_formation.players) for t in i.defend.team]
    req = P.PeakSetDefendFormationC2S()
    for t in (teams[2], teams[1], teams[0]):
        req.pf_info.add(card_gid_list=[p.info.inst_id for p in t])
    await dispatch(a, peak.MSG_SET_DEFEND, req.SerializeToString())
    rep = P.PeakSetDefendFormationS2C.FromString(a.last(peak.MSG_SET_DEFEND))
    assert list(rep.power) == [i.defend.power[2], i.defend.power[1], i.defend.power[0]], list(rep.power)

    br, scores = await quick_series(a, real)
    hs, aw = br.player_grade.home.peak_score, br.player_grade.away.peak_score
    print(f"quick series vs {enemy}: {scores}, winner {br.result.winner}, me {hs.old_score}->{hs.new_score}, "
          f"enemy {aw.old_score}->{aw.new_score}")
    assert hs.old_score == 1000 and (hs.new_score > 1000) == (br.result.winner == 1)
    after = info(a)
    assert after.challenge.challenge_times == 1 and after.week_challenge_times == 1
    dl = peak.entry(enemy)
    assert dl["records"][0]["type"] in (P.PCR_DEFEND_FAIL, P.PCR_DEFEND_SUCCEED)

    await dispatch(a, peak.MSG_INFO)
    idx = next(k for k, p in enumerate(info(a).challenge.challenge_list))
    br, scores = await live_series(a, idx)
    print(f"live series: {scores}, winner {br.result.winner}, score {br.player_grade.home.peak_score.old_score}"
          f"->{br.player_grade.home.peak_score.new_score}")

    await dispatch(a, peak.MSG_RECORD)
    rec = P.PeakRecordLookS2C.FromString(a.last(peak.MSG_RECORD))
    print("records", [(P.PeakChangeResult.Name(r.pcr_result), r.enemy_name, r.own_change_score, len(r.bo3_data))
                      for r in rec.record])
    assert len(rec.record) == 2 and all(2 <= len(r.bo3_data) <= 3 for r in rec.record)

    await dispatch(a, peak.MSG_RESET)
    assert P.PeakResetChallengeListS2C.FromString(a.last(peak.MSG_RESET)).challenge_data.challenge_up_times == 1

    a.account.set_currency(peak.BUY_COST_ITEM, 100)
    await dispatch(a, peak.MSG_BUY, P.PeakBuyChallengeTimesC2S(count=1).SerializeToString())
    assert P.PeakBuyChallengeTimesS2C.FromString(a.last(peak.MSG_BUY)).buy_times == 1
    assert a.account.get_currency(peak.BUY_COST_ITEM) == 100 - peak.BUY_PRICES[0]

    await dispatch(a, peak.MSG_WEEK, P.PeakGetWeekRewardC2S(index=0).SerializeToString())
    wk = P.PeakGetWeekRewardS2C.FromString(a.last(peak.MSG_WEEK))
    print("week chest (needs 3 series, played 2): err", wk.err)
    assert wk.err != 0
    await quick_series(a, 0)
    await dispatch(a, peak.MSG_WEEK, P.PeakGetWeekRewardC2S(index=0).SerializeToString())
    assert P.PeakGetWeekRewardS2C.FromString(a.last(peak.MSG_WEEK)).week_reward == 1

    await dispatch(a, peak.MSG_LADDER, P.PeakGetLadderRewardC2S(conf_id=1).SerializeToString())
    assert P.PeakGetLadderRewardS2C.FromString(a.last(peak.MSG_LADDER)).ladder_reward == 2

    # score formula against the captured records
    for own, enemy_score, won, want in [(2161, 2166, True, 22), (2379, 2387, False, -9), (2594, 2599, False, -9),
                                        (2645 - 18, 2633, True, 18)]:
        got = peak.attack_change(own, enemy_score, won)
        assert got == want, (own, enemy_score, won, got)
    assert peak.defence_change(2313, 2307, True) == 5
    print("OK", TMP)


asyncio.run(main())
