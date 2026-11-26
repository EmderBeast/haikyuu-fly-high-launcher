"""Offline test of the home's daily comfort reward (0x2315 HomeComfortRewardC2S{level = -1}), against the
Garena capture (see homestead.py). Temporary accounts, no port.
  python test_comfort.py
"""
import asyncio
import random
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-comfort-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import homestead  # noqa: E402
import freepack  # noqa: E402
import pack  # noqa: E402
from missions import Missions  # noqa: E402

P = matches.P
checks = []


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


async def ask(c, level=-1):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_COMFORT](c, P.HomeComfortRewardC2S(level=level).SerializeToString())
    return [m for m, _ in c.sent]


async def main():
    # the captured claim: comfort 5085 -> level 20 -> 227 x3900, 117 x48 and the box 102000011
    row = homestead.comfort_row(5085)
    check("comfort 5085 is level 20", row and row["ID"] == 20)
    check("comfort 49 has no level yet, 50 is level 1",
          homestead.comfort_row(49) is None and homestead.comfort_row(50)["ID"] == 1)
    got = dict(homestead.daily_pack(row, random.Random(1)))
    pool = {(r[1], r[2]) for r in pack._pairs(pack.GIFTS[1074]["Randomreward"])}
    box = {(i, n) for i, n in got.items() if i not in (227, 117)} or {(102, got.get(102, 0))}
    check("level 20: 227 x3900 and 117 x48 as in the capture", got.get(227, 0) >= 3900 and got.get(117) == 48, str(got))
    check("the box is opened: one entry of gift 1074, the box itself is not given",
          102000011 not in got and (102, 2500) in pool, str(box))

    c = FakeClient(990901)
    c.account = accounts.Account(990901, accounts.CAPTURED)
    c.missions = Missions(c.account)
    info = P.HomeInfoS2C.FromString(c.account.b.pb("0x2301"))
    level = homestead.comfort_row(info.base_info.comfort.total)
    homestead.entry(c)["comfort_next"] = 0  # claimable
    before = {i: c.account.get_currency(i) for i in (227, 117)}
    now = int(time.time())
    order = await ask(c)
    check("order as captured: change, popup, (mission), reply",
          order[-1] == homestead.MSG_COMFORT and 0x0404 in order and order.index(0x0404) > 0, str(order))
    reply = P.HomeComfortRewardS2C.FromString(c.sent[-1][1])
    check("reply: level -1 and the next 06:00",
          reply.level == -1 and reply.err == 0 and reply.comfort_daily_reward_unix == freepack.day_start(now) + 86400)
    want = dict(__import__("monthcard").pairs(level["DailyGiftPack"]))
    check("the level's daily pack is credited",
          c.account.get_currency(227) - before[227] >= want[227] and c.account.get_currency(117) - before[117] == want[117],
          f"level {level['ID']}")
    popup = P.NotifyRewardsS2C.FromString(next(p for m, p in c.sent if m == 0x0404))
    check("popup without the unopened box", all(i.conf_id != 102000011 for i in popup.items) and len(popup.items) >= 2)

    coins = c.account.get_currency(117)
    order = await ask(c)
    reply2 = P.HomeComfortRewardS2C.FromString(c.sent[-1][1])
    check("asked again the same day: only the time again, nothing credited",
          order == [homestead.MSG_COMFORT] and reply2.comfort_daily_reward_unix == reply.comfort_daily_reward_unix
          and c.account.get_currency(117) == coins)

    homestead.entry(c)["comfort_next"] = now - 10  # the next day
    order = await ask(c)
    check("the next day it is given again", 0x0404 in order and c.account.get_currency(117) - coins == want[117])

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
