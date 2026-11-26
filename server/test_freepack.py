"""Offline test of the shop's free packs (0x2002 RechargeTestC2S), against the Garena captures and the
client's renewal rule (see freepack.py). Temporary accounts, no port.
  python test_freepack.py
"""
import asyncio
import calendar
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-freepack-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import freepack  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []
GOLD_PACK = 150001  # group 4530, weekly, 100 x70000: one of the three captured claims


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def claim(c, row):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[freepack.MSG_PACK](c, P.RechargeTestC2S(id=row).SerializeToString())
    return [m for m, _ in c.sent]


async def main():
    # renewal as the client counts down (game time zone UTC+1: 06:00 = 05:00 UTC)
    wed = calendar.timegm((2026, 11, 18, 12, 0, 0))
    check("daily: since 06:00 today", freepack.period_start(1, wed) == calendar.timegm((2026, 11, 18, 5, 0, 0)))
    check("weekly: since Monday 06:00", freepack.period_start(2, wed) == calendar.timegm((2026, 11, 16, 5, 0, 0)))
    check("monthly: since the 1st 06:00", freepack.period_start(3, wed) == calendar.timegm((2026, 11, 1, 5, 0, 0)))
    early = calendar.timegm((2026, 11, 16, 4, 0, 0))  # Monday before 06:00 still belongs to last week
    check("before 06:00 the old day counts", freepack.period_start(2, early) == calendar.timegm((2026, 11, 9, 5, 0, 0)))

    row = freepack.free_row(GOLD_PACK)
    check("captured pack: free, weekly, 100 x70000",
          row and row["LimitGroup"] == 4530 and row["LimitType"] == 2 and row["ItemReward"] == [8, 100, 16, 70000])

    c = FakeClient(990801)
    await server.handle_login(c, wire.build([(1, 0, 990801), (7, 2, b"token")]))
    gold = c.account.get_currency(100)
    now = int(time.time())
    order = await claim(c, GOLD_PACK)
    check("order as captured: RechargeInfoS2C, change, popup, reply",
          order[0] == freepack.MSG_INFO and order[-2:] == [freepack.MSG_NOTIFY_REWARDS, freepack.MSG_PACK]
          and len(order) == 4, str(order))
    info = P.RechargeInfoS2C.FromString(c.sent[0][1])
    lim = info.limits[0]
    check("RechargeInfoS2C: the group with num 1, start 06:00 of today, reset = now",
          not info.all and len(info.limits) == 1 and lim.limit_group == 4530 and lim.num == 1
          and lim.start_time == freepack.day_start(now) and abs(lim.last_reset_time - now) <= 5)
    popup = P.NotifyRewardsS2C.FromString(c.sent[-2][1])
    check("popup: 100 x70000, not from a card", [(i.conf_id, i.number) for i in popup.items] == [(100, 70000)]
          and popup.origin == 0)
    check("gold credited", c.account.get_currency(100) - gold == 70000)
    check("reply is the empty RechargeTestS2C", c.sent[-1][1] == b"")

    order = await claim(c, GOLD_PACK)
    check("second claim in the same week: refused, nothing given",
          order == [freepack.MSG_PACK] and P.RechargeTestS2C.FromString(c.sent[-1][1]).err == P.FAILED
          and c.account.get_currency(100) - gold == 70000)

    # a week later the pack is there again
    e = freepack.entry(c, 4530)
    e["reset"] -= 8 * 86400
    e["start"] -= 8 * 86400
    order = await claim(c, GOLD_PACK)
    info = P.RechargeInfoS2C.FromString(c.sent[0][1])
    check("next week: claimable again, num back to 1",
          len(order) == 4 and info.limits[0].num == 1 and c.account.get_currency(100) - gold == 140000)

    msgs = freepack.on_login(c)
    check("login tells the client what was claimed here",
          msgs and P.RechargeInfoS2C.FromString(msgs[0][1]).limits[0].limit_group == 4530)

    paid = next(i for i, r in freepack.ROWS.items() if str(r.get("Price")) not in ("0", "") and r.get("Platform") == 1)
    gold = c.account.get_currency(100)
    order = await claim(c, paid)
    check("a pack that costs money is not given", order == [freepack.MSG_PACK] and c.account.get_currency(100) == gold
          and freepack.free_row(paid) is None)

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
