"""Offline test of the weekly activity "Bonus Training" (weekly.py): own counts at login, ticket cost,
rewards from weeklyactivity.json, the certain success after SpecialCount draws, the activity's missions, message
order as captured, a new period. Temporary accounts.
  python test_weekly.py
"""
import asyncio
import random
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-weekly-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import gameconfig  # noqa: E402
import matches  # noqa: E402
import weekly  # noqa: E402
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


class NoLuck:
    """random stand-in: every draw fails unless the certain success comes."""
    def random(self):
        return 0.99

    def choices(self, pop, weights):
        return [pop[0]]


async def main():
    # the table as captured: 202 x 12 per draw, 12 %, certain success after 10, EXP 1000 for a failure
    assert weekly.BASE == [(202, 12)] and weekly.FAIL == [(102, 1000)] and weekly.SPECIAL == 10
    assert weekly.CHANCE == 0.12 and weekly.TICKET == 201 and weekly.TASK_TYPES == [14, 15]

    c = await login(9871)
    i = P.WeeklyActivityInfoS2C.FromString(c.last(weekly.MSG_INFO))
    begin, end = weekly.period()
    print("login info:", i.weekly_activity_id, i.train_count, i.special_train_count, i.begin_unix == begin, i.end_unix == end)
    assert i.weekly_activity_id == 200 and i.train_count == 0 and i.special_train_count == 10
    assert (i.begin_unix, i.end_unix) == (begin, end) and end - begin == 14 * 86400

    # the certain success: nine failures, the tenth draw succeeds, the counter is full again
    d = {"count": 0, "special": 10}
    rewards, special = weekly.draw(d, 10, NoLuck())
    print("ten draws without luck:", rewards, special, d)
    assert rewards == [(202, 120), (102, 9000 + 2000)] and special and d == {"count": 10, "special": 10}
    d = {"count": 0, "special": 10}
    weekly.draw(d, 3, NoLuck())
    assert d["special"] == 7  # three failures since the last success
    # any real roll: 120 points, every draw either EXP or one success item, EXP only in the table's steps
    for seed in range(200):
        d = {"count": 0, "special": 10}
        rw = dict(weekly.draw(d, 10, random.Random(seed))[0])
        items = rw.get(102000009, 0) + rw.get(102000010, 0)
        assert rw[202] == 120 and items <= 10 and rw.get(102, 0) >= 1000 * (10 - items) - 0

    # a ten-training on the wire: ticket cost, rewards, mission progress, message order
    acc = c.account
    acc.set_item(201, 25)
    tid = next(t for t, r in gameconfig.TASKS.items() if r.get("TaskType") == 14 and r.get("TaskSubTargetType") == 20003
               and (r.get("Value") or 0) <= 10)
    acc.tasks[tid] = wire.build([(1, 0, tid), (2, 0, 0), (3, 0, 0)])
    exp = acc.get_currency(102) if acc.is_currency(102) else 0
    c.sent.clear()
    await server.MESSAGE_HANDLERS[weekly.MSG_TRAIN](c, P.WeeklyActivityTrainC2S(is_train_one=False).SerializeToString())
    ids = [m for m, _ in c.sent]
    r = P.WeeklyActivityTrainS2C.FromString(c.last(weekly.MSG_TRAIN))
    got = {x.conf_id: x.number for x in r.rewards.items}
    print("ten-training:", r.err, r.train_count, r.special_train_count, r.is_special, got, [hex(m) for m in ids])
    assert r.err == 0 and r.train_count == 10 and acc.item_count(201) == 15 and got[202] == 120
    assert ids[0] == 0x0802 and ids[-1] == weekly.MSG_TRAIN and ids[-2] == 0x0E02
    assert acc.task_state(tid) != 0  # finished: the target is at most 10
    if acc.is_currency(102):
        assert acc.get_currency(102) - exp == got.get(102, 0)

    # one draw costs one ticket; too few tickets: error, nothing taken
    await server.MESSAGE_HANDLERS[weekly.MSG_TRAIN](c, P.WeeklyActivityTrainC2S(is_train_one=True).SerializeToString())
    assert P.WeeklyActivityTrainS2C.FromString(c.last(weekly.MSG_TRAIN)).train_count == 11 and acc.item_count(201) == 14
    acc.set_item(201, 5)
    await server.MESSAGE_HANDLERS[weekly.MSG_TRAIN](c, P.WeeklyActivityTrainC2S(is_train_one=False).SerializeToString())
    assert P.WeeklyActivityTrainS2C.FromString(c.last(weekly.MSG_TRAIN)).err != 0 and acc.item_count(201) == 5

    # the captured round case: round 2 at 110 trainings, the training to 120 finishes 14201 and in the same
    # TaskChange the round mission 15201 ("Complete All Round 2 Missions") goes to 1
    import missions as missions_mod
    m = c.missions
    for t in range(14201, 14208):
        acc.tasks[t] = wire.build([(1, 0, t), (2, 0, 0), (3, 0, 110)])
    acc.tasks[15201] = wire.build([(1, 0, 15201), (2, 0, 0), (3, 0, 0)])
    m.changed_tasks = []
    weekly.mission_progress(m, 120)
    changed = {t: (acc.task_state(t), wire.get(wire.parse(acc.task(t)), 3, 0)) for t in m.changed_tasks}
    print("round case:", {t: changed[t] for t in (14201, 14202, 15201)})
    assert changed[14201] == (missions_mod.TASK_FINISH, 120) and changed[14202] == (0, 120)
    assert changed[15201] == (0, 1)
    m.changed_tasks = []
    weekly.mission_progress(m, 120)  # nothing new: no change for the round mission
    assert 15201 not in m.changed_tasks

    # a new period starts again from zero
    d = weekly.state(9871, acc, now=end + 60)
    assert d["count"] == 0 and d["special"] == 10 and d["reset_tasks"]
    print("OK", TMP)


asyncio.run(main())
