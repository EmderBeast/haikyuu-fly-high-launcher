"""Offline test of the assistant (0x3703 ShareAssistSet, 0x3704 ShareAssistDo, 0x3701 ShareDataInfo at login)
against the Garena capture decrypted_1 (0048 ShareAssistDoS2C). Temporary accounts, no port.
  python test_assistant.py
"""
import asyncio
import struct
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-assist-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import gameconfig  # noqa: E402
import wire  # noqa: E402

checks = []
# capture decrypted_1/0048_55_4: AssistantRewardUnit of function 19 and 17 (items as (id, count))
GARENA_19 = [(108, 10), (137, 240)]
GARENA_17 = [(107, 80), (137, 2340), (102, 15000), (100, 50000), (507001002, 2), (507001007, 5)]
DAILY_DONE = [2000001, 2000002, 2000007, 2000010]  # the four dailies claimed in that capture (0043)


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def units(payload):
    """ShareAssistDoS2C -> [(func_type, [(id, count)])]"""
    out = []
    for num, wt, v in wire.parse(payload):
        if num == 2:
            u = wire.parse(v)
            items = [(wire.get(wire.parse(i), 2), wire.get(wire.parse(i), 3)) for n, _, i in u if n == 3]
            out.append((wire.get(u, 1), items))
    return out


def set_req(func, is_open, arg=0):
    unit = [(1, 0, func), (2, 0, is_open)] + ([(3, 0, arg)] if arg else [])
    return wire.build([(1, 2, wire.build(unit))])


def finish(acc, m, tid):
    if acc.task(tid) is None:  # starter accounts lack some missions of the captured account
        acc.tasks[tid] = wire.build([(1, 0, tid)])
    m._set(tid, accounts.TASK_FINISH, gameconfig.TASKS[tid].get("Value") or 1)


def info(c):
    body = next(p for m, p in c.sent if m == server.MSG_ASSIST_INFO)
    return {wire.get(wire.parse(v), 1): (wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0))
            for n, _, v in wire.parse(body) if n == 2}


async def main():
    gid = 990701
    c = await login(gid)
    acc, m = c.account, c.missions
    base = info(c)
    check("login sends the baseline settings as ShareDataInfo", base.get(5) == (1, 2) and base.get(17, (0, 0))[0] == 0,
          f"{base}")

    # switch the mission functions 17 and 19 on (closed in the baseline)
    c.sent.clear()
    await server.handle_share_assist_set(c, set_req(17, 1))
    await server.handle_share_assist_set(c, set_req(19, 1))
    reply = wire.parse([p for mid, p in c.sent if mid == server.MSG_ASSIST_SET][-1])
    check("set: answer echoes set_data, no error", wire.get(reply, 1) is None
          and wire.parse(wire.get(reply, 2)) == [(1, 0, 19), (2, 0, 1)])
    c.sent.clear()
    await server.handle_share_assist_set(c, set_req(99, 1))
    check("set: unknown function refused", wire.parse(c.sent[-1][1]) == [(1, 0, 1)])

    # the capture's state: four dailies and one type-23 mission completed, no activity points yet
    for tid in DAILY_DONE + [23001049]:
        finish(acc, m, tid)
    m.activation["daily"] = 0
    for cid, row in gameconfig.ACTIVITY_CHESTS.items():
        if row.get("Type") == 1:
            m.chests[cid] = 0
    other_daily = [t for t in acc.finished_tasks() if gameconfig.TASKS[t].get("TaskType") in (1, 23)
                   and t not in DAILY_DONE + [23001049]]
    for tid in other_daily:  # only the capture's four
        m._set(tid, 0, 0)
    exp0, gold0 = acc.exp, acc.get_currency(100)
    c.sent.clear()
    await server.handle_share_assist_do(c, b"")
    got = units(next(p for mid, p in c.sent if mid == server.MSG_ASSIST_DO))
    check("functions in gamehelperconf Order (19 before 17), as in the capture", [f for f, _ in got] == [19, 17],
          f"{[f for f, _ in got]}")
    check("function 19 rewards = Garena's", dict(got).get(19) == GARENA_19, f"{dict(got).get(19)}")
    check("function 17 rewards = Garena's (4 dailies + chests 1-4)", dict(got).get(17) == GARENA_17,
          f"{dict(got).get(17)}")
    check("missions claimed", all(acc.task_state(t) == accounts.TASK_OVER for t in DAILY_DONE + [23001049]))
    check("daily chests 1-4 received, 5 (100 points) not",
          [m.chests.get(i) for i in range(1, 6)] == [1, 1, 1, 1, 0], f"{[m.chests.get(i) for i in range(1, 6)]}")
    check("gold +50000 credited (plus level-up rewards of the manager EXP)", acc.get_currency(100) - gold0 >= 50000,
          f"{acc.get_currency(100) - gold0}")
    check("manager EXP 106 credited (240 + 2340)", acc.exp - exp0 == 2580 or acc.level > 1, f"{acc.exp - exp0}")
    check("no reward popup", all(mid != server.MSG_REWARD_POPUP for mid, _ in c.sent)
          if hasattr(server, "MSG_REWARD_POPUP") else True)

    # again: nothing left -> empty answer
    c.sent.clear()
    await server.handle_share_assist_do(c, b"")
    check("nothing to do: empty answer", c.sent == [(server.MSG_ASSIST_DO, b"")], f"{c.sent}")

    # switched off -> not run
    finish(acc, m, 2000003)
    await server.handle_share_assist_set(c, set_req(17, 0))
    c.sent.clear()
    await server.handle_share_assist_do(c, b"")
    check("closed function is skipped", acc.task_state(2000003) == accounts.TASK_FINISH
          and units(c.sent[-1][1]) == [])

    # function 6: the stored activity stamina, as in the capture (0028/0029: 128.2 stored, stamina 360 -> 488)
    await server.handle_share_assist_set(c, set_req(6, 1))
    m.activation["physical"] = 128.2
    acc.set_currency(server.STAMINA, 360)
    c.sent.clear()
    await server.handle_share_assist_do(c, b"")
    check("function 6 rewards = Garena's (105 128)", units(c.sent[-1][1]) == [(6, [(105, 128)])],
          f"{units(c.sent[-1][1])}")
    check("stamina 360 -> 488, 0.2 stays stored", acc.get_currency(server.STAMINA) == 488
          and abs(m.activation["physical"] - 0.2) < 1e-6)
    ids = [mid for mid, _ in c.sent]
    check("activation (TaskChange) before the stamina", ids[:2] == [server.MSG_TASK_CHANGE, server.MSG_CURRENCY_CHANGE],
          f"{[hex(i) for i in ids]}")
    act = wire.parse(wire.get(wire.parse(c.sent[0][1]), 2))
    check("physical_num sent as float32", abs(struct.unpack("<f", struct.pack("<I", wire.get(act, 5)))[0] - 0.2) < 1e-6)

    # the activity page's own button (0x0E07 TaskActiveExtractPhysical{num})
    m.activation["physical"] = 3.5
    c.sent.clear()
    await server.handle_extract_physical(c, wire.build([(1, 0, 5)]))
    check("0x0E07: only whole stored points (3 of 5 wanted)", acc.get_currency(server.STAMINA) == 491
          and c.sent[-1] == (0x0E07, b""))
    c.sent.clear()
    await server.handle_extract_physical(c, wire.build([(1, 0, 1)]))
    check("0x0E07: nothing stored -> refused", c.sent == [(0x0E07, wire.build([(1, 0, 1)]))])
    m.activation["physical"] = 50
    acc.set_currency(server.STAMINA, server._memory_max(acc) - 10)
    await server.handle_extract_physical(c, wire.build([(1, 0, 50)]))
    check("0x0E07: not above MemoryMax", acc.get_currency(server.STAMINA) == server._memory_max(acc)
          and m.activation["physical"] == 40)

    # settings survive a new login
    c2 = await login(gid)
    s = info(c2)
    check("settings saved per account", s.get(19) == (1, 0) and s.get(17) == (0, 0) and s.get(5) == (1, 2), f"{s}")
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
