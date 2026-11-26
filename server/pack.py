"""
Using items from the bag: 0x0805 PackItemUseC2S{inst_id, number, chooses, config_id}.

Shown by the Garena captures (Haikyu-Fly-High-Analyse/capture, decrypted_3 and decrypted_runs4; only the
server's side was recorded, the request fields are from the proto):
  - gift boxes (item.json Useway 1, Droptype 1, Itemresult = gift.json ID) with random content
    (Gifttype 2): one draw per box, the same reward added up - 18 boxes 102000006 gave 18 pieces over 13
    kinds, 21 boxes 102000009 gave 21 over 8, 2 boxes 102000010 gave 2 card items
  - the order of the server's messages: PackItemChange (0x0802) for the used item (no count once it is
    used up), the changes for what was won (PackItemChange, one PlayerCardChange per new card),
    NotifyRewards (0x0404) with everything won, then the empty PackItemUseS2C
  - a card item among the rewards (item.json Droptype 2, Itemresult [card, star]) becomes that card at
    that star at once (603003106 -> card 3106 at star 4); the popup lists the card item

Assumptions (no capture):
  - fixed content (Gifttype 1, and the fixed part of Gifttype 3) is given once per box
  - the weight in front of a random entry ("weight:item:count") is a relative chance; every captured
    box has weight 1 throughout
  - a request that cannot be served (item missing, too few) is answered with error FAILED

Not served here (replayed as before, see the open points in docs/garena-abgleich.md): boxes to choose
from (Gifttype 4, the meaning of `chooses` is not in a capture) and the other kinds of usable items.
"""
import logging
import random

import gameconfig
import matches
import pvp
import wire
from accounts import card_info

P = matches.P
log = logging.getLogger("hfh")

MSG_ITEM_CHANGE, MSG_ITEM_USE, MSG_CARD_CHANGE, MSG_NOTIFY_REWARDS = 0x0802, 0x0805, 0x0902, 0x0404
USE, DROP_GIFT, DROP_CARD = 1, 1, 2  # item.json Useway / Droptype
FIXED, RANDOM, FIXED_AND_RANDOM = 1, 2, 3  # gift.json Gifttype (4 = choose)
REWARD_ITEMS = 2  # gift.json Rewardtype of every captured box

ITEMS = {r["ID"]: r for r in pvp._cfg("item")}
GIFTS = {r["ID"]: r for r in pvp._cfg("gift")}


def _pairs(value) -> list[tuple[int, ...]]:
    """'a:b' or ['w:a:b', ...] -> [(a, b)] / [(w, a, b)]."""
    rows = [value] if isinstance(value, str) else (value if isinstance(value, list) else [])
    out = []
    for row in rows:
        try:
            out.append(tuple(int(x) for x in str(row).split(":")))
        except ValueError:
            pass
    return out


def gift_of(item_id: int) -> dict | None:
    """The gift.json row a bag item opens, if it is a box this module serves."""
    item = ITEMS.get(item_id, {})
    if item.get("Useway") != USE or item.get("Droptype") != DROP_GIFT:
        return None
    gift = GIFTS.get(item.get("Itemresult")) if isinstance(item.get("Itemresult"), int) else None
    if not gift or gift.get("Rewardtype") != REWARD_ITEMS or gift.get("Gifttype") not in (FIXED, RANDOM, FIXED_AND_RANDOM):
        return None
    return gift


def open_boxes(gift: dict, number: int, rng=random) -> list[tuple[int, int]]:
    """What `number` boxes give, the same reward added up (order of first appearance)."""
    won: dict[int, int] = {}
    kind = gift.get("Gifttype")
    if kind in (FIXED, FIXED_AND_RANDOM):
        for row in _pairs(gift.get("Fixedreward")):
            if len(row) == 2:
                won[row[0]] = won.get(row[0], 0) + row[1] * number
    if kind in (RANDOM, FIXED_AND_RANDOM):
        pool = [r for r in _pairs(gift.get("Randomreward")) if len(r) == 3 and r[0] > 0]
        if pool:
            for _, item, count in rng.choices(pool, weights=[r[0] for r in pool], k=number):
                won[item] = won.get(item, 0) + count
    return list(won.items())


def card_of(item_id: int) -> tuple[int, int] | None:
    """(card conf id, star) for a card item."""
    item = ITEMS.get(item_id, {})
    res = item.get("Itemresult")
    if item.get("Droptype") == DROP_CARD and isinstance(res, list) and len(res) == 2 and res[0] in gameconfig.PLAYERS:
        return res[0], res[1]
    return None


async def handle_item_use(client, pb: bytes):
    req = P.PackItemUseC2S.FromString(pb)
    acc = client.account
    item_id = acc.item_by_inst(req.inst_id) or req.config_id
    number = max(1, req.number)
    gift = gift_of(item_id)
    if gift is None:  # not a box we can open: as before
        log.info(f"  ITEM USE {item_id} x{number}: not served, captured reply")
        await pvp.HOOKS["generic"](client, pb, MSG_ITEM_USE)
        return
    have = acc.item_count(item_id)
    if have < number:
        log.info(f"  ITEM USE {item_id} x{number}: only {have}")
        await client.send_packet(MSG_ITEM_USE, P.PackItemUseS2C(error=P.FAILED).SerializeToString())
        return
    await client.send_packet(MSG_ITEM_CHANGE, wire.build([(1, 2, acc.set_item(item_id, have - number))]))
    won = open_boxes(gift, number)
    plain = [(i, n) for i, n in won if card_of(i) is None]
    await pvp.HOOKS["grant_rewards"](client, plain, False)
    for item, n in won:
        card = card_of(item)
        for _ in range(n if card else 0):
            inst, raw = acc.add_card(card[0])
            raw = wire.build(wire.replace(wire.parse(raw), 6, card[1]))
            acc.cards[-1] = raw
            await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(raw))]))
    acc.save()
    await client.send_packet(MSG_NOTIFY_REWARDS,
                             wire.build([(1, 2, wire.build([(2, 0, i), (3, 0, n)])) for i, n in won]))
    await client.send_packet(MSG_ITEM_USE, b"")
    log.info(f"  ITEM USE {item_id} x{number} (gift {gift['ID']}): {won}")


HANDLERS = {MSG_ITEM_USE: handle_item_use}
