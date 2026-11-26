"""Offline test of "Quick Star Up" (0x090C PlayerCardCultivateC2S): level and star in one request,
costs from the tables, messages in Garena's order (capture decrypted_runs4). Temporary accounts, no port.
  python test_card_cultivate.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-cultivate-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import gameconfig  # noqa: E402
import wire  # noqa: E402

checks = []


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


def new_card(acc, conf, level, star):
    inst = acc._new_inst()
    raw = wire.build([(1, 0, inst), (2, 0, 1), (3, 0, conf), (4, 0, level), (6, 0, star)])
    acc.put_card(raw)
    return inst


def request(inst, level, star, consume=(), star_lists=None):
    fields = [(1, 0, inst), (2, 0, level), (3, 0, star)] + [(4, 0, i) for i in consume]
    for num, ids in (star_lists or {}).items():
        fields += [(num, 0, i) for i in ids]
    return wire.build(fields)


async def main():
    c = FakeClient(990601)
    await server.handle_login(c, wire.build([(1, 0, 990601), (7, 2, b"token")]))
    acc = c.account
    acc.set_currency(gameconfig.GOLD, 10_000_000)
    acc.set_currency(gameconfig.CARD_EXP, 1_000_000)

    # the capture's case: card 1303 level 1 star 2 -> level 44 star 4 with 5 materials
    main_card = new_card(acc, 1303, 1, 2)
    mats = [new_card(acc, 1101 + i, 1, 2) for i in range(5)]
    gold0, exp0 = acc.get_currency(gameconfig.GOLD), acc.get_currency(gameconfig.CARD_EXP)
    c.sent.clear()
    await server.handle_card_cultivate(c, request(main_card, 44, 4, mats[:3], {7: mats[3:4], 8: mats[4:]}))
    f = wire.parse(acc.get_card(main_card))
    check("card is level 44 star 4", (wire.get(f, 4), wire.get(f, 6)) == (44, 4))
    check("EXP cost as in the capture (300120)", exp0 - acc.get_currency(gameconfig.CARD_EXP) == 300120,
          f"{exp0 - acc.get_currency(gameconfig.CARD_EXP)}")
    want_gold = sum(gameconfig.STAR_LIMITS[s]["Gold"] for s in (3, 4))
    check("gold = upgradestar Gold of star 3 and 4", gold0 - acc.get_currency(gameconfig.GOLD) == want_gold)
    check("all 5 materials consumed", all(acc.get_card(i) is None for i in mats))
    ids = [m for m, _ in c.sent if m in (server.MSG_CURRENCY_CHANGE, server.MSG_CARD_CHANGE, 0x090C)]
    check("messages in Garena's order (gold, EXP, removed, card, answer)",
          ids == [server.MSG_CURRENCY_CHANGE, server.MSG_CURRENCY_CHANGE, server.MSG_CARD_CHANGE,
                  server.MSG_CARD_CHANGE, 0x090C], f"{[hex(i) for i in ids]}")
    check("answer is empty (no error)", next(p for m, p in c.sent if m == 0x090C) == b"")

    # level only (the capture's first request: no gold, no cards)
    other = new_card(acc, 1105, 40, 4)
    gold0 = acc.get_currency(gameconfig.GOLD)
    c.sent.clear()
    await server.handle_card_cultivate(c, request(other, 60, 4))
    check("level only: level 60, no gold", wire.get(wire.parse(acc.get_card(other)), 4) == 60
          and acc.get_currency(gameconfig.GOLD) == gold0)
    check("level only: one currency message (EXP)",
          sum(1 for m, _ in c.sent if m == server.MSG_CURRENCY_CHANGE) == 1)

    # refused: too few materials, level over the target star's cap, unknown card
    poor = new_card(acc, 1201, 1, 2)
    one = new_card(acc, 1202, 1, 2)
    c.sent.clear()
    await server.handle_card_cultivate(c, request(poor, 20, 4, [one]))
    check("too few materials: refused, nothing changed",
          next(p for m, p in c.sent if m == 0x090C) == wire.build([(1, 0, 1)])
          and acc.get_card(one) is not None and wire.get(wire.parse(acc.get_card(poor)), 6) == 2)
    c.sent.clear()
    await server.handle_card_cultivate(c, request(poor, 80, 2))
    check("level above the star cap: refused", next(p for m, p in c.sent if m == 0x090C) == wire.build([(1, 0, 1)]))
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
