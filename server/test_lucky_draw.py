"""Offline test of the Daily Lucky Draw (luckydraw.py, local activity type 11) against the Garena capture
captures/dizzy2711: free spin first (0x0405 257 -> 0), then a ticket (0x0802 item 258 -1), prize credited
without a pop-up, 0x2802 update (count / over_count +1, prize in the log), reply 0x2809 {id}; milestones 1 and 2
claimed with one 0x280A (100 x30000 + 202000002 x10, pop-up, `rewards` [1, 2]); no spin left -> refused; the
free spin again after 06:00 UTC; the week's count and milestones again after Monday 06:00 UTC. Temporary accounts.
  python test_lucky_draw.py
"""
import asyncio
import random
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-luckydraw-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import luckydraw as L  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


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


def t11(payload, cls):
    for a in cls.FromString(payload).activity_list:
        if a.type == 11:
            return P.ActivityDayTurntable.FromString(a.data)


async def main():
    c = FakeClient(991401)
    await server.handle_login(c, wire.build([(1, 0, 991401), (7, 2, b"token")]))
    acc = c.account
    d0 = t11(next(pb for m, pb in c.sent if m == 0x2801), P.LocalActivityInfoS2C)
    check("login: own data, this week's count 0, no milestones (not the capture's 12)",
          d0.over_count == 0 and list(d0.rewards) == [], f"over {d0.over_count}, rewards {list(d0.rewards)}")
    check("login: one free spin today (currency 257 = 1)", acc.get_currency(L.FREE) == 1)
    acc.set_item(L.TICKET, 16)
    count0 = d0.count

    L.random.seed(1)
    c.sent = []
    await server.MESSAGE_HANDLERS[0x2809](c, b"")
    ids = [m for m, _ in c.sent]
    rep = P.LocalActivityDayTurntableDoS2C.FromString(c.sent[-1][1])
    check("free spin: 0x0405 free counter first, 0x2802, reply 0x2809 last, no pop-up",
          ids[0] == 0x0405 and 0x2802 in ids and ids[-1] == 0x2809 and 0x0404 not in ids, str([hex(i) for i in ids]))
    check("free spin used, ticket kept", acc.get_currency(L.FREE) == 0 and acc.item_count(L.TICKET) == 16)
    check("reply id is a prize row", rep.err == 0 and rep.id in L.PRIZES)
    upd = t11(next(pb for m, pb in c.sent if m == 0x2802), P.LocalActivityUpdateS2C)
    check("update: count +1, over_count 1, prize in the log",
          upd.count == count0 + 1 and upd.over_count == 1 and upd.log[-1].id == L._pair(L.PRIZES[rep.id]["GiftsId"])[0])

    c.sent = []
    await server.MESSAGE_HANDLERS[0x2809](c, b"")
    check("second spin takes a ticket (0x0802 first)", c.sent[0][0] == 0x0802 and acc.item_count(L.TICKET) == 15)

    gold = acc.get_currency(100)
    c.sent = []
    await server.MESSAGE_HANDLERS[0x280A](c, b"")
    pops = [pb for m, pb in c.sent if m == 0x0404]
    items = sorted((wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0)) for pb in pops for n, _, v in wire.parse(pb) if n == 1)
    check("milestones 1 + 2 with one request: 100 x30000, 202000002 x10, as captured",
          items == [(100, 30000), (202000002, 10)] and acc.get_currency(100) == gold + 30000, str(items))
    upd = t11(next(pb for m, pb in c.sent if m == 0x2802), P.LocalActivityUpdateS2C)
    check("update: rewards [1, 2], reply 0x280A last", list(upd.rewards) == [1, 2] and c.sent[-1][0] == 0x280A)
    c.sent = []
    await server.MESSAGE_HANDLERS[0x280A](c, b"")
    check("nothing more reached: refused", P.LocalActivityDayTurntableCountRewardS2C.FromString(c.sent[-1][1]).err != 0)

    acc.set_item(L.TICKET, 0)
    c.sent = []
    await server.MESSAGE_HANDLERS[0x2809](c, b"")
    check("no free spin, no ticket: refused", P.LocalActivityDayTurntableDoS2C.FromString(c.sent[-1][1]).err != 0)

    d = L.state(991401, acc)
    d["day"] -= 86400
    check("next day 06:00: one free spin again", L.new_day(991401, acc) and acc.get_currency(L.FREE) == 1)
    d["week"] -= 7 * 86400
    d2 = L.state(991401, acc)
    check("next Monday 06:00: week's count and milestones start again", d2["over"] == 0 and d2["rewards"] == [])

    # weights: free spins use FreeProbability, tickets PayProbability
    rng = random.Random(5)
    free = [L.draw(True, rng) for _ in range(20000)]
    pay = [L.draw(False, rng) for _ in range(20000)]
    check("row 1 (0.30 % with tickets) is rarer on free spins", free.count(1) < pay.count(1), f"{free.count(1)} / {pay.count(1)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
