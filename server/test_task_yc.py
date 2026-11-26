"""Offline test of 0x0E0A TaskYcReceiveRewards against the Garena capture (tasks 23001051 + 24001005):
TaskChange with both claimed first, then the currency update and one pop-up entry per item (108: 5 from the
mission + 60 from the auto-opened box 105000047 = 65), then the reply with the ids. Temporary accounts.
  python test_task_yc.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-taskyc-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import gameconfig  # noqa: E402
import wire  # noqa: E402

TASKS = [23001051, 24001005]


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def main():
    c = FakeClient(9911)
    await server.handle_login(c, wire.build([(1, 0, 9911), (7, 2, b"token")]))
    acc = c.account
    assert gameconfig.task_rewards(24001005) == [(108, 60)]  # box 105000047 opens to its Fixedreward
    for tid, progress in zip(TASKS, (1, 4)):
        acc.tasks[tid] = wire.build([(1, 0, tid), (2, 0, accounts.TASK_FINISH), (3, 0, progress)])
    acc.set_currency(108, 9930)

    c.sent.clear()
    req = wire.build([(1, 2, b"".join(wire.varint(t) for t in TASKS))])  # as captured: packed ids
    await server.MESSAGE_HANDLERS[0x0E0A](c, req)
    ids = [m for m, _ in c.sent]
    print([hex(i) for i in ids])
    assert ids[0] == server.MSG_TASK_CHANGE and ids[-1] == 0x0E0A
    assert ids.index(0x0405) < ids.index(server.MSG_NOTIFY_REWARDS)
    change = [wire.parse(v) for n, _, v in wire.parse(c.sent[0][1]) if n == 1]
    assert {wire.get(f, 1, 0): wire.get(f, 2, 0) for f in change} == {t: accounts.TASK_OVER for t in TASKS}
    popup = [wire.parse(v) for _, _, v in wire.parse(next(p for m, p in c.sent if m == server.MSG_NOTIFY_REWARDS))]
    assert [(wire.get(f, 2, 0), wire.get(f, 3, 0)) for f in popup if wire.get(f, 2, 0) == 108] == [(108, 65)]
    assert acc.get_currency(108) == 9995  # captured 9930 -> 9995
    reply = wire.parse(c.sent[-1][1])
    assert accounts._packed(wire.get(reply, 2, b"")) == TASKS and wire.get(reply, 1, 0) == 0

    c.sent.clear()  # claimed already: error, nothing credited
    await server.MESSAGE_HANDLERS[0x0E0A](c, req)
    assert [m for m, _ in c.sent] == [0x0E0A] and wire.get(wire.parse(c.sent[0][1]), 1, 0) == 1
    assert acc.get_currency(108) == 9995
    print("OK", TMP)


asyncio.run(main())
