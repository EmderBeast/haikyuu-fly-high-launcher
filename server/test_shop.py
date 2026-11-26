"""Offline test of the in-game shop (shop.py, 0x1603 ShopBuy) against the Garena capture: goods 1101305 x1
(shop 10611): 450 of item 202 (645 -> 195), 0x2F03 shelf 3 900 -> 1350, item 318 x1 with pop-up, 0x1602 with
buy_num 2 -> 3, empty reply - in that order; LimitNum 3 then refuses; the counter comes back in the login's
0x1601; too little to pay is refused. Temporary accounts.
  python test_shop.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-shop-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import shop  # noqa: E402
import weekly  # noqa: E402
import wire  # noqa: E402

P = matches.P
BUY = P.ShopBuyC2S(goods_id=1101305, num=1).SerializeToString()  # captured: 0008f99b431001


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


async def main():
    assert BUY == bytes.fromhex("08f99b431001")
    c = await login(9951)
    acc = c.account
    acc.set_item(202, 645)
    shop._bought(9951)["1101305"] = 2  # the capture's counter before the buy
    weekly.state(9951, acc).setdefault("shelves", {})["3"] = 900

    c.sent.clear()
    await server.MESSAGE_HANDLERS[shop.MSG_BUY](c, BUY)
    ids = [m for m, _ in c.sent]
    assert ids == [0x0802, weekly.MSG_COST, 0x0802, server.MSG_NOTIFY_REWARDS, shop.MSG_GOODS, shop.MSG_BUY], \
        [hex(i) for i in ids]
    assert acc.item_count(202) == 195
    assert c.sent[1][1] == wire.build([(1, 0, 3), (2, 0, 1350)])  # captured 0x2F03
    popup = [wire.parse(v) for _, _, v in wire.parse(c.sent[3][1])]
    assert [(wire.get(f, 2, 0), wire.get(f, 3, 0)) for f in popup] == [(318, 1)]
    g = P.ShopGoodsChangeS2C.FromString(c.sent[4][1]).goods_list[0]
    assert (g.goods_id, g.buy_num) == (1101305, 3) and c.sent[5][1] == b""
    w = P.WeeklyActivityInfoS2C.FromString(weekly.info(9951, acc))
    assert {s.shelves: s.integral_cost for s in w.shelves_integral_cost}[3] == 1350

    c.sent.clear()  # LimitNum 3 reached
    await server.MESSAGE_HANDLERS[shop.MSG_BUY](c, BUY)
    assert [m for m, _ in c.sent] == [shop.MSG_BUY] and P.ShopBuyS2C.FromString(c.sent[0][1]).err == 1
    assert acc.item_count(202) == 195

    c2 = await login(9951)  # the counter comes back with the shops
    info = P.ShopInfoS2C.FromString(next(p for m, p in c2.sent if m == shop.MSG_INFO))
    got = {g.goods_id: g.buy_num for s in info.shop_list for g in s.goods_list}
    assert got[1101305] == 3

    # too little to pay: another goods of the same shop with item 202 at 0
    c2.account.set_item(202, 0)
    other = next(i for i, r in shop.GOODS.items() if r.get("CurrencyID") == 202 and i != 1101305)
    c2.sent.clear()
    await server.MESSAGE_HANDLERS[shop.MSG_BUY](c2, P.ShopBuyC2S(goods_id=other, num=1).SerializeToString())
    assert [m for m, _ in c2.sent] == [shop.MSG_BUY] and P.ShopBuyS2C.FromString(c2.sent[0][1]).err == 1
    print("OK", TMP)


asyncio.run(main())
