"""Player cards from fragments, dismantling cards, and the auto-dismantle list, per account.

  0x0903 PlayerCardComposeC2S{data[CardComposeUnit{conf_id = fragment item, num = cards}]} -> {error}
  0x0904 PlayerCardDecomposeC2S{inst_ids} -> {error}
  0x090D PlayerCardAutoResolveC2S{pids (on), un_pids (off)} -> {error, setting = the whole list}
  cards: 0x0902 PlayerCardChangeS2C{players, opt CREATE 0 / REMOVE 2}

No capture of these requests exists. Belegt from the client code (decoded/analysis/hotfix_il_full.txt) and
the tables:
  - compose: conf_id is the fragment item piecemerge.Pieceid (401000000 + player), num the number of
    cards; each card takes piecemerge.Pieceamount fragments (PlayerUIUtils::ComposePlayerCard,
    PlayerChipViewLogic::OnExchangeBtnEvent); copies of an owned player are allowed; the card bag holds
    constant 10018 (3000) cards (CheckBagSpace). A new card is level 1 at its starting star with its
    skills (accounts.make_card; 1302 captured cards are star 2 level 1 with these skills)
  - dismantle: recycle row Leveltype * 10000 + star * 1000 + level gives Expback (item 102) and Goldback
    (100), reslove row Rare * 10 + star gives Lowticket (103) / Highticket (104)
    (PlayerDecomposeUtils::BuildDecomposeResource); the client does not let locked cards or cards of a
    line-up be chosen
  - auto list: player conf ids switched on / off; the reply carries the whole list, the login 0x0901
    PlayerCardPackS2C.setting the same list
Annahmen:
  - cards of any saved line-up or locked cards are refused (FAILED); a dismantled card's memory and
    potentials come off it first
  - rewards with the reward window; order: items, cards, reply
  - what Garena dismantles with the auto list on its own (new draws) is not built: the list is stored and sent
"""
import json
import logging

import gameconfig
import matches
import wire

P = matches.P
log = logging.getLogger("hfh")

MSG_COMPOSE, MSG_DECOMPOSE, MSG_AUTO = 0x0903, 0x0904, 0x090D
CREATE, REMOVE = 0, 2
BAG_LIMIT = 3000  # constant 10018
EXP_ITEM, GOLD, LOW_TICKET, HIGH_TICKET = 102, 100, 103, 104


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


PIECES = {r["Pieceid"]: r for r in _load("piecemerge") if r.get("Pieceid")}
RECYCLE = {r["ID"]: r for r in _load("recycle")}
RESLOVE = {r["ID"]: r for r in _load("reslove")}


def resources(card) -> list[tuple[int, int]]:
    """What a card gives when dismantled."""
    row = gameconfig.PLAYERS.get(card.conf_id, {})
    rec = RECYCLE.get(row.get("Leveltype", 0) * 10000 + card.star * 1000 + card.level, {})
    res = RESLOVE.get(row.get("Rare", 0) * 10 + card.star, {})
    out = [(EXP_ITEM, rec.get("Expback") or 0), (GOLD, rec.get("Goldback") or 0),
           (LOW_TICKET, res.get("Lowticket") or 0), (HIGH_TICKET, res.get("Highticket") or 0)]
    return [(i, n) for i, n in out if n > 0]


def in_lineup(acc) -> set[int]:
    team = P.TeamInfoS2C.FromString(acc.team_info())
    return {i for f in team.formations for i in f.inst_ids if i}


def _cards_msg(cards, opt) -> bytes:
    from accounts import card_info
    return b"".join(wire.build([(1, 2, card_info(c))]) for c in cards) + (wire.build([(2, 0, opt)]) if opt else b"")


async def handle_compose(client, pb: bytes):
    from accounts import MSG_CARD_CHANGE, MSG_ITEM_CHANGE
    acc = client.account
    req = P.PlayerCardComposeC2S.FromString(pb)
    need: dict[int, int] = {}
    make: list[int] = []
    for u in req.data:
        row = PIECES.get(u.conf_id)
        if row is None or u.num <= 0 or row["ID"] not in gameconfig.PLAYERS:
            make = []
            break
        need[u.conf_id] = need.get(u.conf_id, 0) + u.num * row["Pieceamount"]
        make += [row["ID"]] * u.num
    have = len([c for c in {**acc.b.cards, **{wire.get(wire.parse(c), 1): c for c in acc.cards}} if c not in acc.removed_cards])
    if (not make or any(acc.item_count(i) < n for i, n in need.items()) or have + len(make) > BAG_LIMIT):
        log.info(f"  CARD compose refused {need}")
        return await client.send_packet(MSG_COMPOSE, P.PlayerCardComposeS2C(error=P.FAILED).SerializeToString())
    items = [(1, 2, acc.set_item(i, acc.item_count(i) - n)) for i, n in need.items()]
    new = [acc.add_card(conf)[1] for conf in make]
    acc.save()
    log.info(f"  CARD compose {make}")
    await client.send_packet(MSG_ITEM_CHANGE, wire.build(items))
    await client.send_packet(MSG_CARD_CHANGE, _cards_msg(new, CREATE))
    await client.send_packet(MSG_COMPOSE, P.PlayerCardComposeS2C().SerializeToString())


async def handle_decompose(client, pb: bytes):
    from accounts import MSG_CARD_CHANGE
    acc = client.account
    req = P.PlayerCardDecomposeC2S.FromString(pb)
    busy = in_lineup(acc)
    cards = []
    for inst in dict.fromkeys(req.inst_ids):
        raw = acc.get_card(inst)
        card = P.PlayerCard.FromString(raw) if raw else None
        if card is None or card.locked or inst in busy:
            cards = []
            break
        cards.append(card)
    if not cards:
        log.info(f"  CARD dismantle refused {list(req.inst_ids)}")
        return await client.send_packet(MSG_DECOMPOSE, P.PlayerCardDecomposeS2C(error=P.FAILED).SerializeToString())
    gain: dict[int, int] = {}
    for card in cards:
        for i, n in resources(card):
            gain[i] = gain.get(i, 0) + n
    await _free_gear(client, cards)
    gone = [acc.remove_card(c.inst_id) for c in cards]
    acc.save()
    log.info(f"  CARD dismantle {len(cards)} -> {gain}")
    import pvp
    await pvp.HOOKS["grant_rewards"](client, sorted(gain.items()), True)
    await client.send_packet(MSG_CARD_CHANGE, _cards_msg([g for g in gone if g], REMOVE))
    await client.send_packet(MSG_DECOMPOSE, P.PlayerCardDecomposeS2C().SerializeToString())


async def _free_gear(client, cards):
    """The memory and potentials of the cards come off (memory owner 0, potential owner 0)."""
    import specialty
    acc = client.account
    recs, specs = [], []
    for card in cards:
        if card.recalling:
            raw = acc.all_recallings().get(card.recalling)
            if raw:
                r = P.PlayerRecalling.FromString(raw)
                r.player_inst_id = 0
                acc.put_recalling(r.SerializeToString())
                recs.append(r)
        for inst in card.specialtys:
            s = specialty.get(acc, inst) if inst else None
            if s is not None and s.player_inst_id == card.inst_id:
                s.player_inst_id = 0
                specialty.put(acc, s)
                specs.append(s)
    if recs:
        m = P.RecallingUpdateS2C()
        for r in recs:
            m.recalling_updates.add().CopyFrom(r)
        await client.send_packet(0x3C02, m.SerializeToString())
    if specs:  # freed potentials as REMOVE + CREATE, as admin.py does (an UPDATE would keep the old owner)
        await client.send_packet(specialty.MSG_UPDATE, specialty.update_msg(specs, specialty.REMOVE))
        await client.send_packet(specialty.MSG_UPDATE, specialty.update_msg(specs, specialty.CREATE))


def auto_list(acc) -> list[int]:
    if acc.card_auto is not None:
        return list(acc.card_auto)
    return list(P.PlayerCardPackS2C.FromString(acc.b.pb("0x0901")).setting)


async def handle_auto(client, pb: bytes):
    acc = client.account
    req = P.PlayerCardAutoResolveC2S.FromString(pb)
    off = set(req.un_pids)
    have = [i for i in auto_list(acc) if i not in off]
    have += [i for i in req.pids if i not in have and gameconfig.PLAYERS.get(i, {}).get("Rare") in (1, 2, 3, 4)]
    acc.card_auto = have
    acc.save()
    await client.send_packet(MSG_AUTO, P.PlayerCardAutoResolveS2C(setting=have).SerializeToString())


def pack_info(acc, raw: bytes) -> bytes:
    """0x0901 at login with this account's auto list."""
    if acc.card_auto is None:
        return raw
    m = P.PlayerCardPackS2C.FromString(raw)
    del m.setting[:]
    m.setting.extend(acc.card_auto)
    return m.SerializeToString()


HANDLERS = {MSG_COMPOSE: handle_compose, MSG_DECOMPOSE: handle_decompose, MSG_AUTO: handle_auto}
