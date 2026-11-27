"""Offline test of cards from fragments, dismantling and the auto list (cards.py), captured account in a
temporary folder:
  - 0x0903: 2 cards from 2 x Pieceamount fragments, level 1 / starting star; too few fragments -> FAILED
  - 0x0904: recycle (Expback 102 / Goldback 100) + reslove (tickets 103 / 104); locked or line-up cards
    refused; the card's memory comes off; removed after a new login
  - 0x090D: whole list back, kept in 0x0901 after a new login
  python test_cards.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-cards-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402,F401
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import cards as K  # noqa: E402
import gameconfig  # noqa: E402
import matches  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, acc):
        self.gid, self.sent, self.writer, self.account = acc.gid, [], None, acc

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))


def count(acc, item):
    return acc.get_currency(item) if acc.is_currency(item) else acc.item_count(item)


async def main():
    acc = accounts.Account(9841, accounts.CAPTURED)
    c = FakeClient(acc)
    piece = next(p for p, r in sorted(K.PIECES.items()) if r["ID"] in gameconfig.PLAYERS)
    row = K.PIECES[piece]
    acc.set_item(piece, 2 * row["Pieceamount"] + 7)
    before = set(acc.owned_conf_ids())
    n_cards = len(acc.cards)
    req = P.PlayerCardComposeC2S()
    req.data.add(conf_id=piece, num=2)
    await K.handle_compose(c, req.SerializeToString())
    new = [P.PlayerCard.FromString(x) for x in acc.cards[n_cards:]]
    check("0x0903: two cards of that player, level 1, fragments taken",
          len(new) == 2 and all(x.conf_id == row["ID"] and x.level == 1 for x in new) and acc.item_count(piece) == 7,
          [(x.conf_id, x.level, x.star) for x in new])
    check("0x0903: item change, card CREATE, reply", [m for m, _ in c.sent] == [0x0802, 0x0902, K.MSG_COMPOSE])
    c.sent.clear()
    await K.handle_compose(c, req.SerializeToString())
    check("0x0903: too few fragments -> FAILED", P.PlayerCardComposeS2C.FromString(c.sent[-1][1]).error == P.FAILED)
    assert before is not None

    card = new[0]
    want = dict(K.resources(card))
    rec = P.PlayerRecalling.FromString(acc.add_recalling(next(iter(gameconfig._cached("recalling")))))
    rec.player_inst_id = card.inst_id
    acc.put_recalling(rec.SerializeToString())
    card.recalling = rec.inst_id
    acc.put_card(card.SerializeToString())
    got0 = {i: count(acc, i) for i in want}
    c.sent.clear()
    await K.handle_decompose(c, P.PlayerCardDecomposeC2S(inst_ids=[card.inst_id]).SerializeToString())
    got = {i: count(acc, i) - got0[i] for i in want}
    check("0x0904: recycle + reslove resources", got == want and want.get(K.EXP_ITEM), (got, want))
    check("0x0904: card gone, its memory free", acc.get_card(card.inst_id) is None
          and P.PlayerRecalling.FromString(acc.all_recallings()[rec.inst_id]).player_inst_id == 0)
    check("0x0904: REMOVE then reply", [m for m, _ in c.sent][-2:] == [0x0902, K.MSG_DECOMPOSE])

    busy = next(iter(K.in_lineup(acc)))
    locked = next(i for i, raw in acc.b.cards.items() if P.PlayerCard.FromString(raw).locked and i not in K.in_lineup(acc))
    for inst in (busy, locked):
        c.sent.clear()
        await K.handle_decompose(c, P.PlayerCardDecomposeC2S(inst_ids=[inst]).SerializeToString())
        check(f"0x0904: {'line-up' if inst == busy else 'locked'} card refused",
              acc.get_card(inst) is not None and P.PlayerCardDecomposeS2C.FromString(c.sent[-1][1]).error == P.FAILED)

    free = next(i for i, raw in acc.b.cards.items() if not P.PlayerCard.FromString(raw).locked
                and i not in K.in_lineup(acc) and not P.PlayerCard.FromString(raw).recalling
                and not any(P.PlayerCard.FromString(raw).specialtys))
    await K.handle_decompose(c, P.PlayerCardDecomposeC2S(inst_ids=[free]).SerializeToString())
    rare4 = next(i for i, r in sorted(gameconfig.PLAYERS.items()) if r.get("Rare") == 4)
    rare5 = next((i for i, r in sorted(gameconfig.PLAYERS.items()) if r.get("Rare") == 5), None)
    await K.handle_auto(c, P.PlayerCardAutoResolveC2S(pids=[rare4] + ([rare5] if rare5 else [])).SerializeToString())
    setting = list(P.PlayerCardAutoResolveS2C.FromString(c.sent[-1][1]).setting)
    check("0x090D: whole list, rare 5 not taken", rare4 in setting and (rare5 is None or rare5 not in setting), setting)
    await K.handle_auto(c, P.PlayerCardAutoResolveC2S(un_pids=[rare4]).SerializeToString())
    check("0x090D: switched off again", rare4 not in K.auto_list(acc))
    await K.handle_auto(c, P.PlayerCardAutoResolveC2S(pids=[rare4]).SerializeToString())

    again = accounts.Account(9841, accounts.CAPTURED)
    check("new login: dismantled baseline card gone", again.get_card(free) is None)
    check("new login: auto list in 0x0901",
          list(P.PlayerCardPackS2C.FromString(K.pack_info(again, again.b.pb("0x0901"))).setting) == [rare4])


if __name__ == "__main__":
    if not K.PIECES:
        print("skip: tables missing (gamedata)")
    else:
        asyncio.run(main())
        print(f"{sum(checks)}/{len(checks)} checks passed", TMP)
        raise SystemExit(0 if all(checks) else 1)
