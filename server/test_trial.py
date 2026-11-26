"""Offline test of the Specialty Trial (trial.py): own state at login instead of the owner's capture,
progress after a won stage, Hell score and rewards, weekly reset with tier up, quick win, ranking.
Temporary accounts.
  python test_trial.py
"""
import asyncio
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-trial-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import trial  # noqa: E402
import wire  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

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


class FakeMatch:
    """Enough of a finished match for clubs.boss_score: the own team's attack/defence power."""
    def __init__(self, power):
        self.live = type("L", (), {"power_tot": {0: [power, 0], 20: [999999, 0]}})()


def info(c) -> P.SpecificTrialInfoS2C:
    return P.SpecificTrialInfoS2C.FromString(c.last(trial.MSG_INFO))


async def main():
    a = await login(9851)
    i = info(a)
    print("new player:", i.info.degree, i.info.group_id, i.info.formidable, list(i.open_types),
          [(u.stage_group_id, u.progress) for u in i.stages])
    assert i.info.degree == 1 and i.info.formidable == 0 and i.info.reward_received == 0
    assert i.info.group_id == trial.current_group() and i.open_types and len(i.stages) == 4
    assert all(m != 0x2901 or p is not None for m, p in a.sent)  # the capture is not replayed

    # a won stage of tier 1: progress +1; a lost one or a wrong stage: no change
    row = trial.row_of(i.info.group_id, 1, 1)
    stages = trial._stages(row)
    acc = a.account
    upd = trial.on_result(9851, acc, stages[0], True, None)
    u = next(x for x in P.SpecificTrialInfoS2C.FromString(upd[-1][1]).stages if x.stage_group_id == row["ID"])
    assert u.progress == 1 and u.history_progress == 1
    trial.on_result(9851, acc, stages[0], True, None)  # replaying the cleared stage: no progress
    trial.on_result(9851, acc, stages[1], False, None)
    assert trial.state(9851)["units"][str(row["ID"])]["progress"] == 1

    # the whole tier cleared -> next week tier 2, progress back to 0, history kept
    for k in range(1, 5):
        r = trial.row_of(i.info.group_id, k, 1)
        trial.state(9851)["units"][str(r["ID"])] = {"progress": len(trial._stages(r)), "history": 0}
    next_week = time.time() + 7 * 86400
    d = trial.state(9851, now=next_week)
    assert d["degree"] == 2 and d["units"][str(row["ID"])] == {"progress": 0, "history": len(stages)}

    # quick win: progress 0 < history -> the current stage counts as won
    args = P.PVPRoomArgs(pvp_type=P.FIGHT_SPECIFIC_TRIAL, pvp_args=[0, stages[0]])
    await server.MESSAGE_HANDLERS[0x0306](a, P.PVPQuickBattleC2S(args=args).SerializeToString())
    q = P.PVPQuickBattleS2C.FromString(a.last(0x0306))
    print("quick win:", q.error, q.battle_result.info.room_args.pvp_type, len(q.battle_result.rewards))
    assert q.error == 0 and q.battle_result.info.room_args.pvp_type == P.FIGHT_SPECIFIC_TRIAL
    assert d["units"][str(row["ID"])]["progress"] == 1
    args.pvp_args[1] = stages[3]  # not the current stage
    await server.MESSAGE_HANDLERS[0x0306](a, P.PVPQuickBattleC2S(args=args).SerializeToString())
    assert P.PVPQuickBattleS2C.FromString(a.last(0x0306)).error != 0

    # Hell: score = own team's power (the away ids do not count), formidable = best of the week
    d["degree"] = trial.HELL
    hell = trial.row_of(d["group"], trial.hell_type(), trial.HELL)
    hi = P.SpecificTrialInfoS2C.FromString(trial.info(9851, acc))
    assert list(hi.open_types) == [trial.hell_type()] and [u.stage_group_id for u in hi.stages] == [hell["ID"]]
    first = trial._stages(hell)[0]
    upd = dict(trial.on_result(9851, acc, first, False, FakeMatch(1_200_000)))
    assert P.SpecificTrialUpdateS2C.FromString(upd[trial.MSG_UPDATE]).formidable == 1_200_000
    trial.on_result(9851, acc, first, True, FakeMatch(700_000))  # a weaker run keeps the record
    assert d["formidable"] == 1_200_000

    # rewards: every reached row of the group (Condition <= formidable), once
    reached = [r for r in trial.REWARDS if r["GroupId"] == d["group"] and r["Condition"] <= 1_200_000]
    gold = acc.get_currency(100) if acc.is_currency(100) else 0
    await server.MESSAGE_HANDLERS[trial.MSG_REWARD](a, P.SpecificTrialRewardC2S(id=0).SerializeToString())
    rr = P.SpecificTrialRewardS2C.FromString(a.last(trial.MSG_REWARD))
    print("rewards:", [r["ID"] for r in reached], "mask", bin(rr.reward_received))
    assert rr.err == 0 and rr.reward_received == sum(1 << (r["ID"] & 63) for r in reached)
    if acc.is_currency(100):
        assert acc.get_currency(100) > gold
    await server.MESSAGE_HANDLERS[trial.MSG_REWARD](a, P.SpecificTrialRewardC2S(id=0).SerializeToString())
    assert P.SpecificTrialRewardS2C.FromString(a.last(trial.MSG_REWARD)).err != 0  # nothing new

    # ranking 18 = this week's Hell score
    b = await login(9852)
    data = pvp.player_ranking(9852, P.RANK_FIGHT_SPECIFIC_TRIAL)
    print("ranking:", [(r.gid, r.score) for r in data.rank], "own", data.own.rank)
    assert [(r.gid, r.score) for r in data.rank] == [(9851, 1_200_000)] and data.own.rank == -1
    assert info(b).info.degree == 1
    print("OK", TMP)


asyncio.run(main())
