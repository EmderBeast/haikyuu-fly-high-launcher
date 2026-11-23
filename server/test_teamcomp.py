"""Offline test of Match Streak (teamcomp.py): info at login, competitions of 1 and 3 stages played
as live matches, stars / pass / first reward, star chest, line-ups. Temporary accounts, no port.
  python test_teamcomp.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-streak-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import teamcomp  # noqa: E402
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


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def play_comp(c, comp, team=0):
    """As the client: first stage, then StartNextStage until the last; returns per-stage scores."""
    scores = []
    for stage in teamcomp.comp_stages(comp):
        args = P.PVPRoomArgs(pvp_type=P.FIGHT_TEAMCOMPETITION, pvp_args=[team, stage])
        await server.handle_pvp_create(c, P.PVPCreateC2S(args=args).SerializeToString())
        room = P.PVPCreateS2C.FromString(c.last(0x0302)).info
        assert room.away.formation.players[0].info.conf_id, "no monster team"
        assert room.home.formation.players[0].info.conf_id, "no home team"
        m, n = c.room, 0
        while m.live and not m.live.over and n < 300:
            await server.handle_match_data(c, b"")
            n += 1
        before = len(c.sent)
        await server.handle_pvp_result(c, b"")
        br = P.PVPResultS2C.FromString(c.last(0x0304)).battle_result
        scores.append((br.result.home.bo_statistics[0].score, br.result.away.bo_statistics[0].score))
        new = [mid for mid, _ in c.sent[before:]]
        if stage == teamcomp.comp_stages(comp)[-1]:
            assert new.index(teamcomp.MSG_UPDATE) < new.index(0x0304), new  # stars before the result
    return scores, br


async def main():
    c = await login(9201)
    info = P.TeamCompetitionInfoS2C.FromString(c.last(teamcomp.MSG_INFO))
    groups = [g.competition_group_id for g in info.competition_groups]
    print("groups", groups, "formations", [(f.lineup, sum(1 for i in f.inst_ids if i)) for f in info.formations])
    assert 1 in groups and len(info.formations) == 3

    for comp in (1001, 527001):
        scores, br = await play_comp(c, comp)
        rec = teamcomp.entry(9201)["comps"].get(str(comp))
        lead = sum(h for h, _ in scores) - sum(a for _, a in scores)
        print(f"comp {comp}: stages {scores}, lead {lead}, record {rec}, star mask {br.star}, winner {br.result.winner}")
        assert rec["pass"] == (lead > 0)
        assert rec["star"] == sum(1 for need in teamcomp.star_leads(comp) if lead > 0 and lead >= need)

    # force a 3-star clear of the permanent group's first competition and claim its first chest
    teamcomp.entry(9201)["comps"]["1001"] = {"star": 3, "first": True, "pass": True}
    await server.MESSAGE_HANDLERS[teamcomp.MSG_TAKE_REWARD](
        c, P.TeamCompetitionTakeRewardC2S(competition_group_id=1, star_reward_id=1).SerializeToString())
    assert P.TeamCompetitionTakeRewardS2C.FromString(c.last(teamcomp.MSG_TAKE_REWARD)).err == 0
    upd = P.TeamCompetitionUpdateS2C.FromString(c.last(teamcomp.MSG_UPDATE))
    assert list(upd.competition_groups[0].star_reward_ids) == [1]
    await server.MESSAGE_HANDLERS[teamcomp.MSG_TAKE_REWARD](
        c, P.TeamCompetitionTakeRewardC2S(competition_group_id=1, star_reward_id=2).SerializeToString())
    assert P.TeamCompetitionTakeRewardS2C.FromString(c.last(teamcomp.MSG_TAKE_REWARD)).err != 0  # needs 6 stars

    # save line-ups: swap lineup 0 and 1
    forms = teamcomp.formations(9201, c.account)
    req = P.TeamCompetitionFormationSaveC2S(module=[769])
    req.formation.add(lineup=0, inst_ids=forms[1]["insts"])
    req.formation.add(lineup=1, inst_ids=forms[0]["insts"])
    await server.MESSAGE_HANDLERS[teamcomp.MSG_FORMATION_SAVE](c, req.SerializeToString())
    assert teamcomp.formations(9201, c.account)[0]["insts"] == forms[1]["insts"]

    owner = next(iter(__import__("starter").owner_gids()), None)
    if owner:
        o = await login(owner)
        oi = P.TeamCompetitionInfoS2C.FromString(o.last(teamcomp.MSG_INFO))
        done = sum(1 for g in oi.competition_groups for x in g.competitions if getattr(x, "pass"))
        print("owner: groups", len(oi.competition_groups), "passed competitions kept from the capture:", done)
        assert done > 0
    print("OK", TMP)


asyncio.run(main())
