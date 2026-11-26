"""
In-game shop purchases (0x1603 ShopBuyC2S{goods_id, num}) per player. Before, the request got the captured
empty reply: nothing was paid, nothing given, and the goods counters (0x1601) were always the capture's.

Evidenced (decoded_tshark.json: goods 1101305 x1 in shop 10611, the weekly activity's token shop;
goodsconfig.json):
  - price = goodsconfig.DiscountPrice x num of CurrencyID (1101305: 450 of item 202, 645 -> 195), reward =
    ItemID x AcquireNum x num (318 x1); LimitNum = most buys (1101305: 3, the capture's buy_num 2 -> 3);
  - order: cost (0x0802 item / 0x0405 currency), for the weekly token shop (weeklyactivity.ActivityShop)
    0x2F03 {shelves = goods RotationOrder, integral_cost = tokens spent on that shelf} (shelf 3: 900 -> 1350),
    the reward (0x0802) with its pop-up, 0x1602 ShopGoodsChangeS2C with the goods' GoodsData (buy_num raised,
    refresh_time as before), then the empty 0x1603 reply.
Assumed: too little to pay, an unknown goods id or a buy over LimitNum is refused with error 1 and nothing
else; the shelves' TokenLimit [0, 300, 600] is not checked; the tokens spent per shelf belong to the weekly
activity period (weekly.py state) and start at 0 in a new period.
Not built: when goods counters refresh (RefreshType / BigTimeType) - a bought-out goods stays bought out;
discount groups, limit groups, buy demands.
State: pvp.json "shop" {gid: {goods_id: buy_num}}; at login 0x1601 with these counters.
"""
import pvp
import wire

MSG_INFO, MSG_GOODS, MSG_BUY = 0x1601, 0x1602, 0x1603
GOODS = {r["ID"]: r for r in pvp._cfg("goodsconfig")}
_WEEKLY = next(iter(pvp._cfg("weeklyactivity")), {})
WEEKLY_SHOP = _WEEKLY.get("ActivityShop", 10611)


def _P():
    import matches
    return matches.P


def _num(v) -> int:
    return v if isinstance(v, int) and v > 0 else 0


def _bought(gid: int) -> dict:
    return pvp.state().setdefault("shop", {}).setdefault(str(gid), {})


def _base_goods(acc) -> dict:
    """goods_id -> GoodsData of the baseline's 0x1601."""
    out = {}
    try:
        for s in _P().ShopInfoS2C.FromString(acc.b.pb("0x1601")).shop_list:
            for g in s.goods_list:
                out[g.goods_id] = g
    except Exception:
        pass
    return out


def goods_data(gid: int, acc, goods_id: int):
    P = _P()
    g = P.GoodsData()
    base = _base_goods(acc).get(goods_id)
    if base is not None:
        g.CopyFrom(base)
    g.goods_id = goods_id
    mine = pvp.state().get("shop", {}).get(str(gid), {}).get(str(goods_id))
    if mine is not None:
        g.buy_num = mine
    return g


def info(gid: int, acc) -> bytes:
    """The baseline's ShopInfoS2C with this player's goods counters."""
    P = _P()
    out = P.ShopInfoS2C()
    try:
        out.CopyFrom(P.ShopInfoS2C.FromString(acc.b.pb("0x1601")))
    except Exception:
        pass
    mine = {int(k): v for k, v in pvp.state().get("shop", {}).get(str(gid), {}).items()}
    shops = {s.shop_data.shop_id: s for s in out.shop_list}
    for goods_id, n in mine.items():
        tab = (GOODS.get(goods_id) or {}).get("SecondTabID")
        shop = shops.get(tab)
        if shop is None:
            shop = shops[tab] = out.shop_list.add()
            shop.shop_data.shop_id = tab
        g = next((x for x in shop.goods_list if x.goods_id == goods_id), None) or shop.goods_list.add(goods_id=goods_id)
        g.buy_num = n
    return out.SerializeToString()


async def handle_buy(client, pb: bytes):
    P = _P()
    gid, acc = int(client.gid), client.account
    req = P.ShopBuyC2S.FromString(pb)
    row, num = GOODS.get(req.goods_id), req.num or 1
    cur = goods_data(gid, acc, req.goods_id)
    price = _num((row or {}).get("DiscountPrice")) or _num((row or {}).get("Price"))
    cost_id, limit = (row or {}).get("CurrencyID"), _num((row or {}).get("LimitNum"))
    is_currency = bool(cost_id) and acc.is_currency(cost_id)
    have = (acc.get_currency(cost_id) if is_currency else acc.item_count(cost_id)) if cost_id else 0
    if (row is None or num <= 0 or not row.get("ItemID") or have < price * num
            or (limit and cur.buy_num + num > limit)):
        await client.send_packet(MSG_BUY, P.ShopBuyS2C(err=1).SerializeToString())
        return
    spent = price * num
    if spent:
        if is_currency:
            await client.send_packet(0x0405, wire.build([(1, 2, acc.set_currency(cost_id, have - spent))]))
        else:
            await client.send_packet(0x0802, wire.build([(1, 2, acc.set_item(cost_id, have - spent))]))
    acc.save()
    if row.get("SecondTabID") == WEEKLY_SHOP and spent:
        import weekly
        shelf = _num(row.get("RotationOrder")) or 1
        await client.send_packet(weekly.MSG_COST, weekly.spend(gid, acc, shelf, spent))
    await pvp.HOOKS["grant_rewards"](client, [(row["ItemID"], (_num(row.get("AcquireNum")) or 1) * num)], True)
    cur.buy_num += num
    _bought(gid)[str(req.goods_id)] = cur.buy_num
    pvp.save()
    await client.send_packet(MSG_GOODS, P.ShopGoodsChangeS2C(goods_list=[cur]).SerializeToString())
    await client.send_packet(MSG_BUY, b"")


HANDLERS = {MSG_BUY: handle_buy}
