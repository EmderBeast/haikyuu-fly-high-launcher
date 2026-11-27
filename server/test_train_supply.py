"""Offline test of the weekend training supply (trainsupply.py, local activity type 8): own state at login,
claiming on Saturday / Sunday / Monday (bit 1 << weekday, rows of the level band), refused on other days and
twice, a new week on Tuesday 06:00 UTC. Clock set by the test. Temporary accounts.
  python test_train_supply.py
"""
import asyncio
import calendar
import shutil
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-trainsupply-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import trainsupply as T  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []
NOW = [0.0]
T.time.time = lambda: NOW[0]  # the module's clock


def at(y, m, d, h):
    return float(calendar.timegm((y, m, d, h, 0, 0)))


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def claim(c):
    c.sent = []
    await server.MESSAGE_HANDLERS[0x2803](c, P.LocalActivityRewardC2S(type=8).SerializeToString())
    return c.sent, P.LocalActivityRewardS2C.FromString(c.sent[-1][1])


def popup(sent):
    for mid, pb in sent:
        if mid == 0x0404:
            return sorted((wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0)) for n, _, v in wire.parse(pb) if n == 1)
    return None


def data8(payload, cls):
    for a in cls.FromString(payload).activity_list:
        if a.type == 8:
            return P.ActivityTrainSupplyData.FromString(a.data)


async def main():
    NOW[0] = at(2026, 11, 28, 10)  # Saturday
    c = FakeClient(991101)
    await server.handle_login(c, wire.build([(1, 0, 991101), (7, 2, b"token")]))
    d0 = data8(next(pb for mid, pb in c.sent if mid == 0x2801), P.LocalActivityInfoS2C)
    check("login: own data, nothing claimed this week, next refresh Tuesday 06:00 UTC",
          d0.is_reward == 0 and d0.last_refresh_unix == int(at(2026, 12, 1, 6)), str(d0).replace("\n", " "))
    level = c.account.level
    import gameconfig
    want = sorted(gameconfig.open_rewards(T.reward_items(next(r for r in T.rows(level) if r["RewardTime"] == 6))))

    sent, rep = await claim(c)
    ids = [m for m, _ in sent]
    check("Saturday: claimed, 0x2802 first, pop-up, 0x2803 last", rep.err == 0 and ids[0] == 0x2802 and 0x0404 in ids
          and ids[-1] == 0x2803, str([hex(i) for i in ids]))
    check("Saturday reward of the level band", popup(sent) == want, f"{popup(sent)} / {want}")
    check("bit 1 << 6 set", data8(sent[0][1], P.LocalActivityUpdateS2C).is_reward == 1 << 6)
    _, rep = await claim(c)
    check("Saturday twice: refused", rep.err != 0)

    NOW[0] = at(2026, 11, 29, 5)  # Sunday 05:00 UTC = still Saturday's game day
    _, rep = await claim(c)
    check("before 06:00 UTC it is still Saturday: refused", rep.err != 0)
    NOW[0] = at(2026, 11, 29, 7)
    _, rep = await claim(c)
    NOW[0] = at(2026, 11, 30, 7)  # Monday
    sent, rep2 = await claim(c)
    check("Sunday and Monday claimed: bits 6, 7, 1", rep.err == 0 and rep2.err == 0
          and data8(sent[0][1], P.LocalActivityUpdateS2C).is_reward == (1 << 6) | (1 << 7) | (1 << 1))
    NOW[0] = at(2026, 12, 1, 5)  # Tuesday 05:00 UTC: still Monday, already claimed
    _, rep = await claim(c)
    check("Monday twice: refused", rep.err != 0)
    NOW[0] = at(2026, 12, 2, 12)  # Wednesday
    _, rep = await claim(c)
    check("a weekday without supply: refused", rep.err != 0)
    check("new week from Tuesday 06:00 UTC: nothing claimed", T.state(991101)["bits"] == 0
          and T.state(991101)["week"] == int(at(2026, 12, 1, 6)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
