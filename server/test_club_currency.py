"""Club costs are reported as a currency change (0x0405 NotifyCurrencyChangeS2C, client OnCurrencyChanged),
not as a reward pop-up (0x0404, client OnItemGained): creating a club (300 diamonds) and buying buffet food.
Garena capture decoded_tshark.json 2807 / 2808: 0x0405 = {1={1=id 2=new balance}}, 0x0404 = {1={2=id 3=count}}.
Temporary accounts.
  python test_club_currency.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-clubcur-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import clubs as C  # noqa: E402
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


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def balances(sent, msg_id):
    """{currency: number} from the 0x0405-shaped entries of every msg_id message."""
    out = {}
    for mid, pb in sent:
        if mid == msg_id:
            for n, _, v in wire.parse(pb):
                if n == 1:
                    e = wire.parse(v)
                    out[wire.get(e, 1, 0)] = wire.get(e, 2, 0)
    return out


async def main():
    check("clubs use 0x0405 for currency changes", C.MSG_CURRENCY_CHANGE == 0x0405)
    c = await login(991001)
    acc = c.account
    acc.set_currency(108, 1000)
    c.sent = []
    await server.MESSAGE_HANDLERS[C.MSG_CREATE](c, P.AssociationCreateC2S(name="Nekoma", icon_id=1).SerializeToString())
    await asyncio.sleep(0.05)
    check("create: 0x0405 with the new diamond balance", balances(c.sent, 0x0405).get(108) == 1000 - C.CREATE_COST[1],
          str(balances(c.sent, 0x0405)))
    check("create: no reward pop-up with a currency entry", balances(c.sent, 0x0404) == {})
    food = 205000001  # a buffet food (as in test_clubs.py)
    c.sent = []
    have = acc.get_currency(108)
    await server.MESSAGE_HANDLERS[C.MSG_BUFFET_BUY](c, P.AssociationBuffetBuyC2S(item_id=food, num=2).SerializeToString())
    await asyncio.sleep(0.05)
    ok = P.AssociationBuffetBuyS2C.FromString(next(pb for m, pb in c.sent if m == C.MSG_BUFFET_BUY)).err == 0
    check("buffet buy ok, 0x0405 with diamonds - 2 x food price",
          ok and balances(c.sent, 0x0405).get(108) == have - 2 * C.FOOD_PRICE, str(balances(c.sent, 0x0405)))
    pops = [pb for m, pb in c.sent if m == 0x0404]
    items = [(wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0)) for pb in pops for n, _, v in wire.parse(pb) if n == 1]
    check("buffet buy: the pop-up shows only the bought food", (food, 2) in items and all(i[0] != 0 for i in items), str(items))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
