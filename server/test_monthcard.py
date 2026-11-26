"""Offline test of the month / quarter card's daily reward (0x2003 RechargeCardRewardC2S), following the
client's code and the tables (see monthcard.py). Temporary accounts, no port.
  python test_monthcard.py
"""
import asyncio
import calendar
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-card-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import monthcard  # noqa: E402
import wire  # noqa: E402

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


async def claim(c, *groups):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[monthcard.MSG_CARD_REWARD](
        c, P.RechargeCardRewardC2S(limit_group=groups).SerializeToString())
    return [m for m, _ in c.sent]


async def main():
    check("daily rewards from mallrecharge",
          monthcard.REWARDS == {104011: [(108, 90)], 104021: [(108, 100), (105000045, 1)]}, str(monthcard.REWARDS))

    # the 06:00 rule of the client (game time zone UTC+1): 05:00 UTC
    day = calendar.timegm((2026, 11, 19, 0, 0, 0))
    check("claimed 06:01 -> next 06:00 is tomorrow",
          monthcard.boundary(day + 5 * 3600 + 60) == day + 86400 + 5 * 3600)
    check("claimed 05:59 -> 06:00 of the same day", monthcard.boundary(day + 5 * 3600 - 60) == day + 5 * 3600)
    check("claimed late in the evening -> next morning", monthcard.boundary(day + 22 * 3600) == day + 86400 + 5 * 3600)

    # the captured account has both cards running (start 2025-10-08, 480 / 510 days)
    c = FakeClient(990701)
    c.account = accounts.Account(990701, accounts.CAPTURED)
    have = monthcard.limits(c)
    check("captured account: both cards in its RechargeInfoS2C", set(have) == {104011, 104021}, str(list(have)))
    now = int(time.time())
    running = {g: l.start_time + l.total_day * 86400 > now for g, l in have.items()}
    diamonds = c.account.get_currency(108)
    order = await claim(c, 104011, 104021)
    n = sum(running.values())
    if n:
        check("order: RechargeInfoS2C first, popup, then the reply",
              order[0] == monthcard.MSG_INFO and order[-2:] == [monthcard.MSG_NOTIFY_REWARDS, monthcard.MSG_CARD_REWARD],
              str(order))
        info = P.RechargeInfoS2C.FromString(c.sent[0][1])
        check("RechargeInfoS2C: all = false, the claimed groups with the time of the claim",
              not info.all and {l.limit_group for l in info.limits} == {g for g, r in running.items() if r}
              and all(abs(l.last_receive_time - now) <= 5 for l in info.limits))
        check("the other fields of the entry stay",
              all(l.total_day == have[l.limit_group].total_day and l.start_time == have[l.limit_group].start_time
                  for l in info.limits))
        popup = P.NotifyRewardsS2C.FromString(c.sent[-2][1])
        want = (90 if running[104011] else 0) + (100 if running[104021] else 0)
        check("popup from the card (origin ORIGIN_CARD) with the diamonds of the running cards",
              popup.origin == P.ORIGIN_CARD and {i.conf_id: i.number for i in popup.items}.get(108) == want)
        check("diamonds credited", c.account.get_currency(108) - diamonds == want,
              str(c.account.get_currency(108) - diamonds))
    else:
        check("no running card: nothing given", order == [monthcard.MSG_CARD_REWARD])
    check("reply without error", P.RechargeCardRewardS2C.FromString(c.sent[-1][1]).err == 0)

    # asking again the same day: nothing more
    diamonds = c.account.get_currency(108)
    order = await claim(c, 104011, 104021)
    check("second request the same day: only the reply, nothing credited",
          order == [monthcard.MSG_CARD_REWARD] and c.account.get_currency(108) == diamonds, str(order))

    # the next login tells the client the claim, so it does not ask again
    msgs = monthcard.on_login(c)
    if n:
        info = P.RechargeInfoS2C.FromString(msgs[0][1])
        check("login: the claims as RechargeInfoS2C, not claimable until the next 06:00",
              msgs[0][0] == monthcard.MSG_INFO and all(not monthcard.claimable(l, now) for l in info.limits)
              and all(monthcard.claimable(l, monthcard.boundary(l.last_receive_time)) for l in info.limits))

    # a card that has run out, and a player without a card
    lim = P.RechargeLimitData(limit_group=104011, start_time=now - 40 * 86400, total_day=30, last_receive_time=0)
    check("a card that has run out cannot be claimed", not monthcard.claimable(lim, now))
    s = FakeClient(990702)
    await server.handle_login(s, wire.build([(1, 0, 990702), (7, 2, b"token")]))
    order = await claim(s, 104011)
    check("player without a card: only the reply", order == [monthcard.MSG_CARD_REWARD] and not monthcard.limits(s))

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
