"""Offline test of opening gift boxes from the bag (0x0805 PackItemUseC2S), against what the Garena
captures show (see pack.py). Temporary accounts, no port.
  python test_pack.py
"""
import asyncio
import random
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-pack-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import pack  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []
PIECE_BOX, CARD_BOX = 102000006, 102000010  # gift 1017 (pieces), gift 1061 (card items), both captured


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def entries(payload):
    return [wire.parse(v) for n, _, v in wire.parse(payload) if n == 1]


async def use(c, item, number):
    c.sent.clear()
    inst = c.account.items[item]["inst_id"]
    await server.MESSAGE_HANDLERS[pack.MSG_ITEM_USE](c, P.PackItemUseC2S(inst_id=inst, number=number).SerializeToString())
    return [m for m, _ in c.sent]


async def main():
    random.seed(7)
    c = await login(990401)
    acc = c.account
    acc.set_item(PIECE_BOX, 20)
    acc.set_item(CARD_BOX, 2)

    # 18 piece boxes at once, as in the capture
    before = {i: acc.item_count(i) for i in pack.ITEMS}
    order = await use(c, PIECE_BOX, 18)
    check("order: used item, winnings, popup, reply",
          order[0] == pack.MSG_ITEM_CHANGE and order[-2:] == [pack.MSG_NOTIFY_REWARDS, pack.MSG_ITEM_USE], str(order))
    used = entries(c.sent[0][1])[0]
    check("used item goes down by the number used", wire.get(used, 2) == PIECE_BOX and wire.get(used, 3) == 2)
    popup = {wire.get(e, 2): wire.get(e, 3) for e in entries(c.sent[-2][1])}
    pool = {r[1] for r in pack._pairs(pack.GIFTS[1017]["Randomreward"])}
    check("one draw per box, same reward added up", sum(popup.values()) == 18 and set(popup) <= pool, str(popup))
    check("winnings are in the bag", all(acc.item_count(i) - before.get(i, 0) == n for i, n in popup.items()))
    check("reply is the empty PackItemUseS2C", c.sent[-1][1] == b"")

    # the last 2 piece boxes: the item is used up
    await use(c, PIECE_BOX, 2)
    check("used up: count 0", acc.item_count(PIECE_BOX) == 0)

    # more than there are
    order = await use(c, PIECE_BOX, 1)
    reply = P.PackItemUseS2C.FromString(c.sent[-1][1])
    check("too few: error, nothing given", order == [pack.MSG_ITEM_USE] and reply.error == P.FAILED)

    # 2 card boxes: two cards at the star the card item names
    cards_before = len(acc.cards)
    order = await use(c, CARD_BOX, 2)
    check("one PlayerCardChange per card", order.count(pack.MSG_CARD_CHANGE) == 2, str(order))
    check("two new cards", len(acc.cards) - cards_before == 2)
    popup = {wire.get(e, 2): wire.get(e, 3) for e in entries(c.sent[-2][1])}
    check("popup lists the card items", sum(popup.values()) == 2 and all(pack.card_of(i) for i in popup), str(popup))
    new = [wire.parse(x) for x in acc.cards[-2:]]
    want = sorted(pack.card_of(i) for i, n in popup.items() for _ in range(n))
    check("cards and stars as the card items say", sorted((wire.get(f, 3), wire.get(f, 6)) for f in new) == want,
          str(want))
    check("card items are not left in the bag", all(acc.item_count(i) == 0 for i in popup))

    # a box to choose from is not served: the captured reply as before
    choose = next(i for i, r in pack.ITEMS.items() if r.get("Useway") == 1 and r.get("Droptype") == 1
                  and pack.GIFTS.get(r.get("Itemresult") if isinstance(r.get("Itemresult"), int) else -1, {}).get("Gifttype") == 4)
    acc.set_item(choose, 1)
    await use(c, choose, 1)
    check("box to choose from: untouched", acc.item_count(choose) == 1 and pack.gift_of(choose) is None)

    # fixed content: once per box
    fixed = next(g for g in pack.GIFTS.values() if g.get("Gifttype") == 1 and g.get("Rewardtype") == 2)
    one, three = pack.open_boxes(fixed, 1), pack.open_boxes(fixed, 3)
    check("fixed content times the number of boxes", bool(one) and [(i, n * 3) for i, n in one] == three, str(one))

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
