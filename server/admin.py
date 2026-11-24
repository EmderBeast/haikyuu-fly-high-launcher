"""
Admin Mode: give yourself characters, currencies, items and potentials.

Only active when server/admin.json exists ({"pw_sha256": "<sha256 of the password>"}),
i.e. in the admin build of the launcher package.

Who is admin:
- own (local) server: whoever enters the admin password in game (Settings -> gift code,
  0x2403 SettingGiftCodeC2S); kept in accounts/admin_unlocked.json.
- shared multiplayer server (server/multiplayer.json or HFH_MULTIPLAYER=1): only the OWNER and
  the players the owner made admin (accounts/admins.json). The first player who enters the
  password becomes the owner; after that the password does nothing. Everybody else is a normal
  player: admin switches on their account (infinite stamina, strength) are ignored.

Changes are queued in accounts/admin_queue/<gid>.json (by the launcher's "Admin Mode" window on
the server PC, or by in-game commands), applied at the player's next login
(Account.__init__ -> apply_pending) and reported in accounts/admin_done/<gid>.json.

In-game commands for admins: the gift code field takes "/command [value] [@player]"
(player = name or id, default yourself; applied at that player's next login):
  /gold N  /diamonds N  /exp N  /stamina N  /level N  /items N  /allcards  /maxcards
  /infinite on|off  /power N|off  (all cards' match strength in percent)
Owner only (shared server): /admin <player>  /unadmin <player>

Potentials (PlayerSpecialty) can also be put on / taken off cards in game
(0x1804 / 0x1805), for the baseline ones as well as the created ones.

Queue entries (list, applied in order):
  {"level": 60}
  {"currency": {"100": 9999999, ...}}           conf id -> amount
  {"all_cards": true}                           one card of every character not owned yet
  {"max_cards": true}                           every card to the top star and level 80
  {"items": 999}                                every bag item kind (as in the captured account) to this count
  {"flags": {"infinite_stamina": true}}
  {"specialties": [{"conf": 3051045, "level": 12, "main": 1023,
                    "vices": [[2010, 2], [2023, 1], ...], "count": 1}]}
  {"power": {"<card inst or *>": {"pct": 150, "stats": {"<attr type>": value}} | null}}
      match strength per card ("*" = every card): all 7 stats in percent and/or single stats
      set to a value as shown in game; null removes it. Applied when the team for a match is
      built (matches.home_team -> apply_power), so the match engine uses it.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path

import gameconfig
import wire

log = logging.getLogger("hfh")

HERE = Path(__file__).parent
CONFIG = HERE / "admin.json"
MULTIPLAYER = HERE / "multiplayer.json"  # marks a shared server: Admin Mode is off there
ACCOUNTS = HERE / "accounts"
UNLOCKED = ACCOUNTS / "admin_unlocked.json"
ADMINS = ACCOUNTS / "admins.json"  # shared server: {"owner": "<gid>", "admins": {"<gid>": "<name>"}}
QUEUE = ACCOUNTS / "admin_queue"
DONE = ACCOUNTS / "admin_done"

MSG_GIFT_CODE = 0x2403
MSG_SPECIALTY_ON = 0x1804
MSG_SPECIALTY_OFF = 0x1805
MAX_STAR = 6
SPECIALTY_INST_BASE = 1 << 61  # far away from captured and starter instance ids


def multiplayer() -> bool:
    """Shared server for several players (server/multiplayer.json or HFH_MULTIPLAYER=1)."""
    return os.environ.get("HFH_MULTIPLAYER", "") not in ("", "0") or MULTIPLAYER.exists()


def enabled() -> bool:
    """Admin Mode exists on this server (admin build: server/admin.json)."""
    return CONFIG.exists()


def admins_state() -> dict:
    st = _json(ADMINS, {})
    st.setdefault("owner", None)
    st.setdefault("admins", {})
    return st


def save_admins(st: dict):
    ACCOUNTS.mkdir(exist_ok=True)
    ADMINS.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")


def is_owner(gid) -> bool:
    return enabled() and multiplayer() and str(gid) == str(admins_state().get("owner"))


def is_admin(gid) -> bool:
    """Admin rights: password-unlocked on an own server, owner/granted on a shared server."""
    if not enabled() or not gid:
        return False
    if multiplayer():
        st = admins_state()
        return str(gid) == str(st.get("owner")) or str(gid) in st["admins"]
    return str(gid) in unlocked()


def flag(acc, key: str) -> bool:
    """An Admin Mode account switch, only honoured for admins."""
    return is_admin(acc.gid) and bool(acc.admin_flags.get(key))


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def unlocked() -> dict:
    return _json(UNLOCKED, {})


def is_unlocked(gid: int) -> bool:
    return is_admin(gid)


def check_password(code: str) -> bool:
    want = _json(CONFIG, {}).get("pw_sha256", "")
    return bool(want) and hashlib.sha256(code.strip().encode("utf-8")).hexdigest() == want


# --- applying queued changes ------------------------------------------------------

def _specialty_rows():
    if not gameconfig.AVAILABLE:
        return {}
    rows = json.loads((gameconfig.CFG / "specialty.json").read_text(encoding="utf-8"))
    return {r["ID"]: r for r in rows}


def make_specialty(inst: int, conf: int, level: int, main: int, vices) -> bytes:
    f = [(1, 0, inst), (2, 0, conf)]
    if level:
        f.append((3, 0, level))
    f.append((7, 2, wire.build([(1, 0, main)] + ([(2, 0, level)] if level else []))))
    for attr, lev in vices:
        f.append((8, 2, wire.build([(1, 0, int(attr))] + ([(2, 0, int(lev))] if lev else []))))
    f.append((9, 0, int(time.time())))
    return wire.build(f)


def _apply(acc, action: dict) -> list[str]:
    done = []
    if "level" in action:
        acc.level = max(1, min(int(action["level"]), gameconfig.MAX_PLAYER_LEVEL))
        acc.exp = 0
        done.append(f"Manager-Level {acc.level}")
    for cid, amount in action.get("currency", {}).items():
        acc.set_currency(int(cid), max(0, int(amount)))
        done.append(f"Währung {cid} = {amount}")
    if action.get("all_cards") and gameconfig.AVAILABLE:
        owned = acc.owned_conf_ids()
        new = [c for c in sorted(gameconfig.PLAYERS) if c not in owned]
        for conf in new:
            acc.add_card(conf)
        done.append(f"{len(new)} Charaktere freigeschaltet")
    if action.get("max_cards"):
        cards = {**acc.b.cards, **{wire.get(wire.parse(c), 1): c for c in acc.cards}}
        maxed = []
        for inst, card in cards.items():
            if inst in acc.removed_cards:
                continue
            f = wire.parse(card)
            f = wire.set_varint(f, 6, MAX_STAR)
            f = wire.set_varint(f, 4, gameconfig.level_cap(MAX_STAR))
            maxed.append(wire.build(f))
        acc.cards = maxed  # one pass instead of put_card per card (thousands of cards)
        n = len(maxed)
        done.append(f"{n} Karten auf {MAX_STAR} Sterne / Level {gameconfig.level_cap(MAX_STAR)}")
    if "items" in action:
        from accounts import CAPTURED, PSEUDO_ITEMS
        count = max(0, int(action["items"]))
        n = 0
        # only item kinds a real inventory holds (the captured account's bag): the item table also
        # lists memories, wallpapers etc. that are no bag items and break the client's inventory
        for item_id in sorted(set(CAPTURED.items) | set(acc.b.items) | set(acc.items)):
            if item_id in PSEUDO_ITEMS or acc.is_currency(item_id):
                continue
            acc.set_item(item_id, count)
            n += 1
        done.append(f"{n} Gegenstände auf {count}")
    for key, value in action.get("power", {}).items():
        mods = acc.admin_flags.setdefault("power", {})
        if value is None:
            mods.pop(str(key), None)
            done.append(f"Stärke {key} zurückgesetzt")
        else:
            mods[str(key)] = {"pct": int(value.get("pct", 100)),
                              "stats": {str(t): int(v) for t, v in value.get("stats", {}).items()}}
            done.append(f"Stärke {key}: {mods[str(key)]['pct']} %"
                        + (f", {len(mods[str(key)]['stats'])} Werte gesetzt" if mods[str(key)]["stats"] else ""))
    for key, value in action.get("flags", {}).items():
        acc.admin_flags[key] = bool(value)
        done.append(f"{key} = {bool(value)}")
    if action.get("specialties"):
        rows = _specialty_rows()
        n = 0
        for spec in action["specialties"]:
            conf = int(spec["conf"])
            if conf not in rows:
                continue
            for _ in range(max(1, min(int(spec.get("count", 1)), 50))):
                inst = SPECIALTY_INST_BASE + acc.next_inst
                acc.next_inst += 1
                acc.put_specialty(make_specialty(inst, conf, int(spec.get("level", 0)),
                                                 int(spec["main"]), spec.get("vices", [])))
                n += 1
        done.append(f"{n} Potentials erstellt")
    return done


def apply_pending(acc):
    """Apply the queued Admin Mode changes for this account (called at login).

    Only admins can queue (launcher on the server PC or in-game commands), so the target itself
    need not be admin; admin-only switches stay without effect for normal players (flag())."""
    if not enabled():
        return
    path = QUEUE / f"{acc.gid}.json"
    actions = _json(path, None)
    if actions is None:
        return
    report = []
    for action in actions if isinstance(actions, list) else [actions]:
        try:
            report += _apply(acc, action)
        except Exception as e:  # a bad entry must never block the login
            log.error(f"  ADMIN change failed {action}: {e!r}")
            report.append(f"Fehler: {e}")
    path.unlink(missing_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)
    (DONE / f"{acc.gid}.json").write_text(json.dumps({"time": time.time(), "done": report},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  ADMIN applied for {acc.gid}: {report}")


def apply_power(acc, team):
    """Scale / set the stats of the cards in a match team (PVPTeamInfo) by the Admin Mode settings."""
    mods = (getattr(acc, "admin_flags", None) or {}).get("power") or {}
    if not mods or not is_admin(getattr(acc, "gid", None)):  # normal players: no effect
        return
    import matches
    stat_types = {t for t, _, _ in matches.STATS}
    for p in team.formation.players:
        m = mods.get(str(p.info.inst_id)) or mods.get("*")
        if not m:
            continue
        before = matches.card_power(p.attr)
        for a in p.attr:
            if a.type not in stat_types:
                continue
            if str(a.type) in m.get("stats", {}):
                a.val = int(m["stats"][str(a.type)]) * matches.SCALE
            elif m.get("pct", 100) != 100:
                a.val = a.val * int(m["pct"]) // 100
        # captured cards' power also counts potentials/bonds: scale it with the stat sum
        after = matches.card_power(p.attr)
        p.power = p.power * after // before if before else after
    team.power = sum(p.power for p in team.formation.players[:7])


# --- message handlers -------------------------------------------------------------

def player_names() -> dict:
    """gid -> name of the players this server knows (PvP snapshots, granted admins)."""
    names = {}
    try:
        import pvp
        names.update({g: s.get("name", "") for g, s in pvp.state().get("snapshots", {}).items()})
    except Exception:
        pass
    for g, n in admins_state()["admins"].items():
        names.setdefault(g, n)
    return names


def find_player(text: str) -> str | None:
    """gid for "12345" or a player name (exact, else unique start, case-insensitive)."""
    text = text.strip().lstrip("@")
    if text.isdigit():
        return text
    names = player_names()
    low = text.lower()
    exact = [g for g, n in names.items() if (n or "").lower() == low]
    if len(exact) == 1:
        return exact[0]
    start = [g for g, n in names.items() if (n or "").lower().startswith(low)]
    return start[0] if len(start) == 1 else None


def queue(gid, actions: list):
    QUEUE.mkdir(parents=True, exist_ok=True)
    path = QUEUE / f"{gid}.json"
    old = _json(path, [])
    path.write_text(json.dumps((old if isinstance(old, list) else [old]) + actions, indent=1),
                    encoding="utf-8")


CURRENCY_CMDS = {"/gold": 100, "/diamonds": 108, "/diamanten": 108, "/exp": 102, "/erf": 102,
                 "/stamina": 105, "/ausdauer": 105}


def command(gid, code: str) -> str:
    """Run an admin command from the gift code field; returns a log line."""
    parts = code.split()
    target = str(gid)
    if len(parts) > 1 and parts[-1].startswith("@"):
        target = find_player(parts.pop())
        if target is None:
            return f"unknown player in {code!r}"
    cmd, args = parts[0].lower(), parts[1:]
    num = int(args[0]) if args and args[0].isdigit() else None

    if cmd in ("/admin", "/unadmin"):
        if not is_owner(gid):
            return f"{cmd}: only the owner of a shared server"
        who = find_player(" ".join(args)) if args else None
        if who is None:
            return f"{cmd}: unknown player {' '.join(args)!r}"
        st = admins_state()
        if cmd == "/admin":
            st["admins"][who] = player_names().get(who, "")
        else:
            st["admins"].pop(who, None)
        save_admins(st)
        return f"{cmd} {who} ({st['admins'].get(who, '')})"

    if cmd in ("/rebind", "/unbind"):  # token binding on a shared server (bindings.py)
        import bindings
        if target == str(gid) and len(code.split()) == 1:
            return f"{cmd}: name the player, e.g. {cmd} @Name"
        ok = bindings.approve(target) if cmd == "/rebind" else bindings.release(target)
        return f"{cmd} {target}: {'ok' if ok else 'nothing to do'}"

    if cmd in CURRENCY_CMDS and num is not None:
        action = {"currency": {str(CURRENCY_CMDS[cmd]): num}}
    elif cmd == "/level" and num is not None:
        action = {"level": num}
    elif cmd == "/items" and num is not None:
        action = {"items": num}
    elif cmd == "/allcards":
        action = {"all_cards": True}
    elif cmd == "/maxcards":
        action = {"max_cards": True}
    elif cmd == "/infinite" and args and args[0].lower() in ("on", "an", "off", "aus"):
        action = {"flags": {"infinite_stamina": args[0].lower() in ("on", "an")}}
    elif cmd == "/power" and args and (num is not None or args[0].lower() in ("off", "aus")):
        action = {"power": {"*": None if num is None else {"pct": num}}}
    else:
        return f"unknown command {code!r}"
    queue(target, [action])
    return f"{code!r} queued for {target}"


async def handle_gift_code(client, pb_data: bytes):
    """0x2403 SettingGiftCodeC2S{1=code}: admin password or "/command" for admins."""
    code = wire.get(wire.parse(pb_data), 1, b"")
    code = (code.decode("utf-8", errors="replace") if isinstance(code, bytes) else str(code)).strip()
    gid = getattr(client, "gid", None)
    if enabled() and gid and code.startswith("/"):
        if is_admin(gid):
            log.info(f"  ADMIN command from {gid}: {command(gid, code)}")
        else:
            log.info(f"  ADMIN command {code!r} from {gid} ignored (no admin rights)")
    elif enabled() and gid and check_password(code):
        if multiplayer():
            st = admins_state()
            if not st.get("owner"):
                st["owner"] = str(gid)
                save_admins(st)
                log.info(f"  ADMIN owner of this shared server: {gid}")
            else:
                log.info(f"  admin password from {gid} ignored (owner is {st['owner']})")
        else:
            state = unlocked()
            state[str(gid)] = time.time()
            ACCOUNTS.mkdir(exist_ok=True)
            UNLOCKED.write_text(json.dumps(state, indent=1), encoding="utf-8")
            log.info(f"  ADMIN MODE unlocked for {gid}")
    else:
        log.info(f"  Gift code {code!r} (no effect)")
    await client.send_packet(MSG_GIFT_CODE, wire.build([]))


def _position(rows, conf: int) -> int:
    return rows.get(conf, {}).get("Position") or 0


SLOTS = 6  # PlayerCard field 9: packed potential inst ids, index = specialty Position (0 = empty)


def _slots(card: bytes) -> list[int]:
    ids = _packed(wire.get(wire.parse(card), 9, b""))
    return (ids + [0] * SLOTS)[:SLOTS]


def _with_slots(card: bytes, slots: list[int]) -> bytes:
    f = [x for x in wire.parse(card) if x[0] != 9]
    f = sorted(f + [(9, 2, b"".join(wire.varint(i) for i in slots))], key=lambda x: x[0])
    return wire.build(f)


def _all_cards(acc) -> dict[int, bytes]:
    cards = {**acc.b.cards, **{wire.get(wire.parse(c), 1): c for c in acc.cards}}
    return {i: c for i, c in cards.items() if i not in acc.removed_cards}


def _set_owner(acc, inst: int, card_inst: int) -> bytes | None:
    """PlayerSpecialty field 6 = card it sits on (explicit 0 when free)."""
    spec = acc.get_specialty(inst)
    if spec is None:
        return None
    f = sorted([x for x in wire.parse(spec) if x[0] != 6] + [(6, 0, card_inst)], key=lambda x: x[0])
    spec = wire.build(f)
    acc.put_specialty(spec)
    return spec


async def _send_changes(client, cards: dict[int, bytes], specs: dict[int, bytes]):
    from accounts import MSG_CARD_CHANGE, card_info
    def msg(items, opt):
        return b"".join(wire.build([(1, 2, s)]) for s in items) + (wire.build([(3, 0, opt)]) if opt else b"")

    # the client merges UPDATEs, so a cleared owner (0) would not stick: freed potentials are
    # sent as REMOVE + CREATE (replaced), equipped ones as UPDATE
    freed = [s for s in specs.values() if not wire.get(wire.parse(s), 6, 0)]
    placed = [s for s in specs.values() if wire.get(wire.parse(s), 6, 0)]
    if freed:
        await client.send_packet(0x1802, msg(freed, 2))
        await client.send_packet(0x1802, msg(freed, 0))
    if placed:
        await client.send_packet(0x1802, msg(placed, 1))
    if cards:  # PlayerCardChangeS2C opt UPDATE
        await client.send_packet(MSG_CARD_CHANGE, b"".join(wire.build([(1, 2, card_info(c))]) for c in cards.values())
                                 + wire.build([(2, 0, 1)]))


def _card_of(fields) -> int:
    """The card instance: the first varint field (observed: field 1)."""
    return next((v for _, wt, v in fields if wt == 0), 0)


async def handle_specialty_on(client, pb_data: bytes):
    """0x1804 PlayerSpecialtyOnC2S{1=card inst, then dress entries}: put potentials on a card."""
    acc = client.account
    fields = wire.parse(pb_data)
    log.info(f"  SPECIALTY on {fields}")
    card_inst = _card_of(fields)
    rows = _specialty_rows()
    all_specs = acc.all_specialties()
    wanted = []
    for num, wt, v in fields:
        if wt == 2:  # dress entry or packed ids: every known potential id inside counts
            try:
                inner = [x for _, t, x in wire.parse(v) if t == 0]
            except Exception:
                inner = []
            wanted += [x for x in inner + _packed(v) if x in all_specs and x not in wanted]
        elif wt == 0 and v in all_specs and v not in wanted:
            wanted.append(v)
    cards = _all_cards(acc)
    if card_inst not in cards:
        await client.send_packet(MSG_SPECIALTY_ON, wire.build([]))
        return
    changed_cards, changed_specs = {}, {}
    for inst in wanted:
        pos = _position(rows, wire.get(wire.parse(all_specs[inst]), 2))
        for other_inst, other in cards.items():  # take it off any card it is on now
            slots = _slots(changed_cards.get(other_inst, other))
            if inst in slots and other_inst != card_inst:
                slots = [0 if i == inst else i for i in slots]
                changed_cards[other_inst] = _with_slots(other, slots)
        slots = _slots(changed_cards.get(card_inst, cards[card_inst]))
        if slots[pos] and slots[pos] != inst:
            changed_specs[slots[pos]] = _set_owner(acc, slots[pos], 0)
        slots[pos] = inst
        changed_cards[card_inst] = _with_slots(cards[card_inst], slots)
        changed_specs[inst] = _set_owner(acc, inst, card_inst)
    for c in changed_cards.values():
        acc.put_card(c)
    acc.save()
    await _send_changes(client, changed_cards, {k: v for k, v in changed_specs.items() if v})
    await client.send_packet(MSG_SPECIALTY_ON, wire.build([]))


async def handle_specialty_off(client, pb_data: bytes):
    """0x1805 PlayerSpecialtyOffC2S{1=card inst, 2=positions (packed)}: take potentials off a card."""
    acc = client.account
    fields = wire.parse(pb_data)
    log.info(f"  SPECIALTY off {fields}")
    card_inst = _card_of(fields)
    positions = set()
    for num, wt, v in fields:
        if wt == 2:
            positions |= set(_packed(v))
        elif v != card_inst:
            positions.add(v)
    card = _all_cards(acc).get(card_inst)
    if card is None:
        await client.send_packet(MSG_SPECIALTY_OFF, wire.build([]))
        return
    slots = _slots(card)
    changed_specs = {}
    for pos in range(SLOTS):
        if slots[pos] and (not positions or pos in positions):
            changed_specs[slots[pos]] = _set_owner(acc, slots[pos], 0)
            slots[pos] = 0
    card = _with_slots(card, slots)
    acc.put_card(card)
    acc.save()
    await _send_changes(client, {card_inst: card}, {k: v for k, v in changed_specs.items() if v})
    await client.send_packet(MSG_SPECIALTY_OFF, wire.build([]))


def _packed(data: bytes) -> list[int]:
    vals, pos = [], 0
    while pos < len(data):
        v, pos = wire.read_varint(data, pos)
        vals.append(v)
    return vals


HANDLERS = {
    MSG_GIFT_CODE: handle_gift_code,
    MSG_SPECIALTY_ON: handle_specialty_on,
    MSG_SPECIALTY_OFF: handle_specialty_off,
}
