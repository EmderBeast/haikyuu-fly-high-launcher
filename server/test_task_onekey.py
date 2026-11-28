"""Offline test of "claim all" for missions (0x0E05 TaskReceiveOneKeyC2S) as the client sends it:
receives = repeated TaskReceiveReward{task_type, task_team_id}. Temporary accounts, no port.
  python test_task_onekey.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-onekey-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import missions  # noqa: E402
import gameconfig  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


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


def request(*kinds):
    req = P.TaskReceiveOneKeyC2S()
    for task_type, team in kinds:
        req.receives.add(task_type=task_type, task_team_id=team)
    return req.SerializeToString()


def kind(tid):
    cfg = gameconfig.TASKS.get(tid, {})
    return cfg.get("TaskType"), cfg.get("TaskTeamId")


def finish(c, task_type, n):
    """Marks the first n open missions of a type as completed."""
    m, out = c.missions, []
    for tid in m._tasks_of(task_type):
        if c.account.task_state(tid) == 0 and len(out) < n:
            m._set(tid, accounts.TASK_FINISH, gameconfig.TASKS[tid].get("Value") or 1)
            out.append(tid)
    return out


async def main():
    c = await login(990301)
    acc = c.account
    finish(c, missions.DAILY, 3)
    finish(c, missions.WEEKLY, 2)
    daily = [t for t in acc.finished_tasks() if kind(t)[0] == missions.DAILY]
    weekly = [t for t in acc.finished_tasks() if kind(t)[0] == missions.WEEKLY]
    print(f"     completed: daily {len(daily)}, weekly {len(weekly)}")
    check("test has completed daily and weekly missions", len(daily) >= 3 and len(weekly) >= 2)

    # what the real client sends for the daily page: one entry, team id left at 0
    body = request((missions.DAILY, 0))
    check("request is a list of messages, not packed mission ids", body == bytes([0x0A, 0x02, 0x08, missions.DAILY]), body.hex())
    check("daily page: all completed dailies, no weekly", sorted(server.one_key_tasks(acc, body)) == sorted(daily))
    points = c.missions.activation["daily"]
    await server.handle_task_one_key(c, body)
    check("dailies claimed", all(acc.task_state(t) == accounts.TASK_OVER for t in daily))
    check("weeklies untouched", all(acc.task_state(t) == accounts.TASK_FINISH for t in weekly))
    check("daily activity points booked", c.missions.activation["daily"] > points, f"{points} -> {c.missions.activation['daily']}")
    check("reply sent", c.sent[-1] == (0x0E05, b""))

    # team id narrows it down
    teams = sorted({kind(t)[1] for t in weekly})
    check("wrong team id: nothing", server.one_key_tasks(acc, request((missions.WEEKLY, 999999))) == [])
    check("right team id: its missions", sorted(server.one_key_tasks(acc, request((missions.WEEKLY, teams[0]))))
          == sorted(t for t in weekly if kind(t)[1] == teams[0]))
    check("two kinds in one request", sorted(server.one_key_tasks(acc, request((missions.DAILY, 0), (missions.WEEKLY, 0)))) == sorted(weekly))
    check("empty request: everything completed", sorted(server.one_key_tasks(acc, b"")) == sorted(acc.finished_tasks()))
    await server.handle_task_one_key(c, request((missions.WEEKLY, 0)))
    check("weeklies claimed", all(acc.task_state(t) == accounts.TASK_OVER for t in weekly))

    # Quick Claim also opens the activity chests the new points reach (Emre 28.11.2026)
    m = c.missions
    daily_chests = sorted((r.get("Active") or 0, cid) for cid, r in gameconfig.ACTIVITY_CHESTS.items() if r.get("Type") == 1)
    reached = [cid for need, cid in daily_chests if need <= m.activation["daily"]]
    check("chests reached by the daily points are open", reached and all(m.chests.get(cid) == missions.CHEST_RECEIVED for cid in reached),
          f"points {m.activation['daily']}, chests {reached}")
    later = [cid for need, cid in daily_chests if need > m.activation["daily"]]
    check("chests not reached stay closed", all(m.chests.get(cid) != missions.CHEST_RECEIVED for cid in later))
    # a second daily batch that passes the next threshold opens that chest too, in one TaskChange + pop-up
    if later:
        need = gameconfig.ACTIVITY_CHESTS[later[0]].get("Active")
        more = []
        while m.activation["daily"] + sum(r[1] for t in more for r in (gameconfig.task_rewards(t) or []) if r[0] == missions.DAILY_POINTS) < need:
            got = finish(c, missions.DAILY, 1)
            if not got:
                break
            more += got
        c.sent.clear()
        await server.handle_task_one_key(c, request((missions.DAILY, 0)))
        check("next chest opened by Quick Claim", m.chests.get(later[0]) == missions.CHEST_RECEIVED or not more,
              f"points {m.activation['daily']} / {need}")
        check("one TaskChange for missions and chests", [i for i, _ in c.sent].count(server.MSG_TASK_CHANGE) == 1, [hex(i) for i, _ in c.sent])

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
