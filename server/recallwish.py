"""
Memory recruit wish (0x0F0C LotteryRecallWishC2S{id, item_id}). Before, the request got the captured empty
reply, the wish was lost, and every login sent the captured account's RecallLotteryUnits (0x0F0A).

Evidenced (decoded_tshark.json: pool 27, item 506001023 = memory 23): Garena sends 0x0F0A
LotteryRecallInfoS2C (is_all false) with only that pool's whole RecallLotteryUnit, `wish` = the item, every
other field as before (all_count 260, unlock_time ...), then the empty 0x0F0C reply.
recallgachaconf.WishMenu marks the 20 pools (of 93) that take a wish.
Assumed: a pool without WishMenu or an item that is no memory (recalling.ItemID) is refused with error 1.
Not built: what the wish changes in memory draws (0x0F0B is not built at all).
State: pvp.json "recall_wish" {gid: {pool: item}}; at login the baseline's 0x0F0A with these wishes.
"""
import pvp

MSG_INFO, MSG_WISH = 0x0F0A, 0x0F0C
POOLS = {r["ID"]: r for r in pvp._cfg("recallgachaconf")}
MEMORY_ITEMS = {r.get("ItemID") for r in pvp._cfg("recalling") if r.get("ItemID")}


def _P():
    import matches
    return matches.P


def _wishes(gid: int) -> dict:
    return pvp.state().setdefault("recall_wish", {}).setdefault(str(gid), {})


def units(gid: int, acc) -> dict:
    """pool -> RecallLotteryUnit: the baseline's with this player's wishes."""
    P = _P()
    out = {}
    try:
        for u in P.LotteryRecallInfoS2C.FromString(acc.b.pb("0x0F0A")).lotterys:
            out[u.pool_id] = u
    except Exception:
        pass
    for pool, item in pvp.state().get("recall_wish", {}).get(str(gid), {}).items():
        u = out.setdefault(int(pool), P.RecallLotteryUnit(pool_id=int(pool)))
        u.wish = item
    return out


def info(gid: int, acc) -> bytes:
    P = _P()
    out = P.LotteryRecallInfoS2C()
    try:
        out.CopyFrom(P.LotteryRecallInfoS2C.FromString(acc.b.pb("0x0F0A")))
    except Exception:
        out.is_all = True
    mine = units(gid, acc)
    for u in out.lotterys:
        if u.pool_id in mine:
            u.CopyFrom(mine.pop(u.pool_id))
    out.lotterys.extend(mine.values())
    return out.SerializeToString()


async def handle_wish(client, pb: bytes):
    P = _P()
    req = P.LotteryRecallWishC2S.FromString(pb)
    if not (POOLS.get(req.id) or {}).get("WishMenu") or req.item_id not in MEMORY_ITEMS:
        await client.send_packet(MSG_WISH, P.LotteryRecallWishS2C(err=1).SerializeToString())
        return
    _wishes(client.gid)[str(req.id)] = req.item_id
    pvp.save()
    unit = units(int(client.gid), client.account)[req.id]
    await client.send_packet(MSG_INFO, P.LotteryRecallInfoS2C(lotterys=[unit]).SerializeToString())
    await client.send_packet(MSG_WISH, b"")


HANDLERS = {MSG_WISH: handle_wish}
