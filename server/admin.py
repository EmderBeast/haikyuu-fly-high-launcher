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
  /card <name>  (every version of that character)   /potential <set name>  (full set, max)
  /infinite on|off  /power N|off  (all cards' match strength in percent)
Owner only (shared server): /admin <player>  /unadmin <player>

Potentials (PlayerSpecialty) can also be put on / taken off cards in game
(0x1804 / 0x1805), for the baseline ones as well as the created ones.

Queue entries (list, applied in order):
  {"level": 60}
  {"currency": {"100": 9999999, ...}}           conf id -> amount
  {"all_cards": true}                           one card of every character not owned yet
  {"cards": [conf, ...]}                        one card of each of these characters
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
ALL_QUEUE = QUEUE / "all.json"  # [{"id": n, "actions": [...]}]: for every own account, once each
LAST_LOGIN = ACCOUNTS / "last_login.json"  # {"gid": ..., "time": ...}: shown in the Admin Mode window

MSG_GIFT_CODE = 0x2403
MSG_SPECIALTY_ON = 0x1804
MSG_SPECIALTY_OFF = 0x1805
MAX_STAR = 6
MEM_MAX_LEVEL, MEM_MAX_STAGE = 80, 5  # recallingconstant 3 (max level per star) and 1 (max stage)
TWIN_BASE = 50000  # card ids from here on are copies of real characters (not obtainable)
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
    # own server: every account on it belongs to the PC's owner - unlocked once = all accounts
    return bool(unlocked())


def is_own(gid) -> bool:
    """Target of "all my accounts": every account on an own server, owner/admins on a shared one."""
    return is_admin(gid)


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
        # 5xxxx: copies of the real characters (opponent/special versions), not obtainable in game
        new = [c for c in sorted(gameconfig.PLAYERS) if c < TWIN_BASE and c not in owned]
        for conf in new:
            acc.add_card(conf)
        done.append(f"{len(new)} Charaktere freigeschaltet")
        # memories (Recalling): one of each kind the account does not have yet
        have = {wire.get(wire.parse(r), 2, 0) for r in acc.all_recallings().values()}
        mem = [m for m in sorted(gameconfig._cached("recalling")) if m not in have]
        for conf in mem:
            acc.add_recalling(conf)
        done.append(f"{len(mem)} Memories dazu")
    if action.get("remove_twins"):
        cards = {**acc.b.cards, **{wire.get(wire.parse(c), 1): c for c in acc.cards}}
        conf = {i: wire.get(wire.parse(c), 3, 0) for i, c in cards.items() if i not in acc.removed_cards}
        swapped = _swap_twins_in_team(acc, conf)
        team = _team_insts(acc)
        gone = [i for i, c in conf.items() if c >= TWIN_BASE and i not in team]
        for inst in gone:
            acc.remove_card(inst)
            acc.removed_cards.add(inst)  # also baseline (starter) cards
        done.append(f"{len(gone)} doppelte Charaktere entfernt"
                    + (f", {swapped} in der Aufstellung durch das Original ersetzt" if swapped else ""))
    if action.get("cards") and gameconfig.AVAILABLE:
        confs = [int(c) for c in action["cards"] if int(c) in gameconfig.PLAYERS]
        for conf in confs:
            acc.add_card(conf)
        done.append(f"{len(confs)} Karte(n): " + ", ".join(card_names().get(c, str(c)) for c in confs))
    if "resonance" in action:
        # {"resonance": {"<inst>"|"*": level}}: threshold (4/6/...), 0 = off, "t<n>" = n-th tier, "max".
        # Admin override: the skill-level requirement of the game does not apply here.
        wanted = action["resonance"]
        cards = {**acc.b.cards, **{wire.get(wire.parse(c), 1): c for c in acc.cards}}
        changed = []
        for inst, card in cards.items():
            if inst in acc.removed_cards:
                continue
            lv = wanted.get(str(inst), wanted.get("*"))
            if lv is None:
                continue
            f = wire.parse(card)
            tiers = gameconfig.resonance_tiers(wire.get(f, 3, 0))
            if not tiers:
                continue
            if lv == "max":
                level = tiers[-1]
            elif str(lv).startswith("t"):
                level = tiers[min(int(str(lv)[1:]), len(tiers)) - 1] if int(str(lv)[1:]) > 0 else 0
            else:
                level = max([t for t in tiers if t <= int(lv)], default=0)
            if wire.get(f, gameconfig.RESONANCE_FIELD, 0) != level:
                changed.append(wire.build(wire.set_varint(f, gameconfig.RESONANCE_FIELD, level)))
        if changed:
            keep = {wire.get(wire.parse(c), 1) for c in changed}
            acc.cards = [c for c in acc.cards if wire.get(wire.parse(c), 1) not in keep] + changed
        done.append(f"Resonance bei {len(changed)} Karte(n) gesetzt")
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
        # full skill resonance (highest tier) on every maxed card
        for i, raw in enumerate(maxed):
            f = wire.parse(raw)
            tiers = gameconfig.resonance_tiers(wire.get(f, 3, 0))
            if tiers:
                maxed[i] = wire.build(wire.set_varint(f, gameconfig.RESONANCE_FIELD, tiers[-1]))
        acc.cards = maxed  # one pass instead of put_card per card (thousands of cards)
        # memories: level 80, tier V (stage 5)
        for inst, r in acc.all_recallings().items():
            f = wire.parse(r)
            for num, val in ((3, MEM_MAX_LEVEL), (4, MEM_MAX_LEVEL), (5, MEM_MAX_STAGE)):
                f = wire.set_varint(f, num, val)
            f = [x for x in f if x[0] != 6]  # no leftover EXP
            acc.recallings[inst] = wire.build(f)
        done.append(f"{len(acc.all_recallings())} Memories auf Level {MEM_MAX_LEVEL} / Tier V, volle Resonance")
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


def _team_insts(acc) -> set[int]:
    """Card instances used in any saved line-up (never removed by a clean-up)."""
    from accounts import _packed
    try:
        team = acc.team or acc.b.pb("0x0B01")
        out = set()
        for num, _, v in wire.parse(team):
            if num != 1:
                continue
            for n2, wt, v2 in wire.parse(v):
                if n2 == 3:
                    out |= set(_packed(v2) if wt == 2 else [v2])
        return out
    except Exception:
        return set()


def _swap_twins_in_team(acc, conf: dict) -> int:
    """In every line-up, a copy (5xxxx) is replaced by the owned original (id - 50000)."""
    from accounts import _packed
    best = {}  # original conf -> an owned instance (the strongest = highest level/star wins later)
    for inst, c in sorted(conf.items()):
        if c < TWIN_BASE:
            best.setdefault(c, inst)
    swapped, out = 0, []
    for num, wt, v in wire.parse(acc.team_info()):
        if num != 1:
            out.append((num, wt, v))
            continue
        fields = wire.parse(v)
        insts = []
        for n2, w2, v2 in fields:
            if n2 == 3:
                insts += _packed(v2) if w2 == 2 else [v2]
        new = []
        for i in insts:
            orig = best.get(conf.get(i, 0) - TWIN_BASE) if conf.get(i, 0) >= TWIN_BASE else None
            if orig is not None and orig not in insts and orig not in new:
                new.append(orig)
                swapped += 1
            else:
                new.append(i)
        if new == insts:
            out.append((num, wt, v))
            continue
        rest = [f for f in fields if f[0] != 3]
        rest.append((3, 2, b"".join(wire.varint(i) for i in new)))
        out.append((1, 2, wire.build(sorted(rest, key=lambda f: f[0]))))
    if swapped:
        acc.team = wire.build(out)
    return swapped


def apply_pending(acc):
    """Apply the queued Admin Mode changes for this account (called at login).

    Only admins can queue (launcher on the server PC or in-game commands), so the target itself
    need not be admin; admin-only switches stay without effect for normal players (flag())."""
    if not enabled():
        return
    try:
        ACCOUNTS.mkdir(exist_ok=True)
        LAST_LOGIN.write_text(json.dumps({"gid": str(acc.gid), "time": time.time()}), encoding="utf-8")
    except OSError:
        pass
    path = QUEUE / f"{acc.gid}.json"
    actions = _json(path, None)
    batch = []
    seen = int(acc.admin_flags.get("all_seen", 0))
    if is_own(acc.gid):  # "all my accounts": each batch once per account
        for entry in _json(ALL_QUEUE, []):
            if int(entry.get("id", 0)) > seen:
                batch += entry.get("actions", [])
                seen = max(seen, int(entry["id"]))
    if actions is None and not batch:
        return
    report = []
    for action in (actions if isinstance(actions, list) else [actions] if actions else []) + batch:
        try:
            report += _apply(acc, action)
        except Exception as e:  # a bad entry must never block the login
            log.error(f"  ADMIN change failed {action}: {e!r}")
            report.append(f"Fehler: {e}")
    if batch:
        acc.admin_flags["all_seen"] = seen
    path.unlink(missing_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)
    (DONE / f"{acc.gid}.json").write_text(json.dumps({"time": time.time(), "done": report},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    log.info(f"  ADMIN applied for {acc.gid}: {report}")
    acc.save()


def queue_all(actions: list):
    """Queue changes for all own accounts (each applies them once at its next login)."""
    QUEUE.mkdir(parents=True, exist_ok=True)
    entries = _json(ALL_QUEUE, [])
    nid = max([int(e.get("id", 0)) for e in entries] + [int(time.time())]) + 1
    entries.append({"id": nid, "actions": actions})
    ALL_QUEUE.write_text(json.dumps(entries, indent=1), encoding="utf-8")


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


_names_cache: dict = {}


def _lang() -> dict:
    if "lang" not in _names_cache:
        rows = json.loads((gameconfig.CFG / "language.json").read_text(encoding="utf-8"))
        _names_cache["lang"] = {r["ID"]: r for r in rows}
    return _names_cache["lang"]


def _text(ref) -> str:
    import re
    m = re.findall(r"\d+", str(ref or ""))
    row = _lang().get(int(m[0])) if m else None
    return (row.get("German") or row.get("English") or "") if row else ""


def card_names() -> dict:
    """card conf id -> shown name (e.g. "TAKANOBU AONE (Nach der Schule)")."""
    if "cards" not in _names_cache:
        _names_cache["cards"] = {c: _text(r.get("Intro")) or str(c) for c, r in gameconfig.PLAYERS.items()}
    return _names_cache["cards"]


def suit_names() -> dict:
    """potential set id -> name (e.g. 101 -> "Präziser Aufschlag")."""
    if "suits" not in _names_cache:
        rows = json.loads((gameconfig.CFG / "specialtysuitconf.json").read_text(encoding="utf-8"))
        _names_cache["suits"] = {r["ID"]: _text(r.get("Name")) or str(r["ID"]) for r in rows}
    return _names_cache["suits"]


def _norm(t: str) -> str:
    return "".join(ch for ch in t.lower() if ch.isalnum())


def find_cards(text: str) -> list[int]:
    """Every card whose name contains all words of text (a number is taken as conf id)."""
    if text.strip().isdigit():
        return [int(text)] if int(text) in gameconfig.PLAYERS else []
    words = [_norm(w) for w in text.split() if _norm(w)]
    out = [c for c, n in card_names().items() if words and all(w in _norm(n) for w in words)]
    normal = [c for c in out if c < 50000]  # 5xxxx: same-named twins (opponent/special versions)
    return sorted(normal or out)[:20]


def full_set(suit: int) -> list[dict]:
    """All 6 places of a potential set, top rarity, max level, its first main and 4 vice attrs."""
    rows = _specialty_rows()
    vices = {}
    try:
        vices = {r["ID"]: r["Viceattr"] for r in json.loads(
            (gameconfig.CFG / "specialtyviceattr.json").read_text(encoding="utf-8"))}
    except Exception:
        pass
    rare = max((r["Rare"] for r in rows.values() if r["SuitID"] == suit), default=0)
    level = {1: 6, 2: 6, 3: 9, 4: 12}.get(rare, 12)
    out = []
    for r in rows.values():
        if r["SuitID"] != suit or r["Rare"] != rare:
            continue
        mains = r["MainattrID"] if isinstance(r["MainattrID"], list) else [r["MainattrID"]]
        pool = vices.get(r.get("ViceattrPool"), [])[:4]
        out.append({"conf": r["ID"], "level": level, "main": mains[0],
                    "vices": [[a, 1 if i < level // 3 else 0] for i, a in enumerate(pool)], "count": 1})
    return out


def find_suit(text: str) -> int | None:
    if text.strip().isdigit():
        return int(text) if int(text) in suit_names() else None
    words = [_norm(w) for w in text.split() if _norm(w)]
    exact = [s for s, n in suit_names().items() if _norm(n) == "".join(words)]
    if exact:
        return exact[0]
    hits = [s for s, n in suit_names().items() if words and all(w in _norm(n) for w in words)]
    return hits[0] if len(hits) == 1 else None


HELP = ("Befehle: /gold N, /diamonds N, /exp N, /stamina N (kommen sofort als Post), /level N, "
        "/items N, /allcards, /maxcards, /card <Name>, /potential <Set>, /infinite on|off, "
        "/power N|off – mit @Name für einen anderen Spieler.")


def notify(gid, text: str, items: list | None = None) -> bool:
    """In-game mail "Admin Mode" (mail.py), optionally with attachments the player claims at once.

    Returns False where there is no mailbox (then nothing was sent)."""
    try:
        import mail
        now = int(time.time())
        mail.send(int(gid), {"id": mail._new_id(), "template": 0, "title": "Admin Mode", "content": text,
                             "items": [list(i) for i in (items or [])], "args": [], "send": now,
                             "end": now + 7 * 86400, "state": mail.P.MAIL_UNREAD})
        return True
    except Exception as e:
        log.warning(f"  ADMIN notify {gid} failed: {e!r}")
        return False


def _name(gid) -> str:
    return player_names().get(str(gid)) or str(gid)


def command(gid, code: str) -> tuple[str, str | None, str | None, str | None]:
    """Run an admin command from the gift code field.

    Returns (log line, message to the sender or None, target gid, message to the target or None).
    Currency commands (/gold N ...) arrive at once as a mail attachment that ADDS N; the other
    commands are queued and work at the next login."""
    parts = code.split()
    target = str(gid)
    if len(parts) > 1 and parts[-1].startswith("@"):
        who = parts.pop()
        target = find_player(who)
        if target is None:
            return f"unknown player in {code!r}", f"Spieler {who[1:]} nicht gefunden.", None, None
    cmd, args = parts[0].lower(), parts[1:]
    num = int(args[0]) if args and args[0].isdigit() else None

    if cmd in ("/admin", "/unadmin"):
        if not is_owner(gid):
            return (f"{cmd}: only the owner of a shared server",
                    f"{cmd} darf nur der Besitzer eines gemeinsamen Servers benutzen.", None, None)
        who = find_player(" ".join(args)) if args else None
        if who is None:
            return (f"{cmd}: unknown player {' '.join(args)!r}",
                    f"Spieler {' '.join(args) or '?'} nicht gefunden.", None, None)
        st = admins_state()
        if cmd == "/admin":
            st["admins"][who] = player_names().get(who, "")
        else:
            st["admins"].pop(who, None)
        save_admins(st)
        granted = cmd == "/admin"
        return (f"{cmd} {who} ({_name(who)})",
                f"{_name(who)} hat {'jetzt' if granted else 'keine'} Admin-Rechte{'' if granted else ' mehr'}.",
                who, ("Du hast jetzt Admin-Rechte auf diesem Server. " + HELP) if granted
                else "Deine Admin-Rechte auf diesem Server wurden entfernt.")

    if cmd in ("/rebind", "/unbind"):  # token binding on a shared server (bindings.py)
        import bindings
        if target == str(gid) and len(code.split()) == 1:
            return (f"{cmd}: name the player, e.g. {cmd} @Name", f"Bitte Spieler angeben, z. B. {cmd} @Name.",
                    None, None)
        ok = bindings.approve(target) if cmd == "/rebind" else bindings.release(target)
        what = "freigegeben" if cmd == "/rebind" else "gelöst"
        return (f"{cmd} {target}: {'ok' if ok else 'nothing to do'}",
                f"Login von {_name(target)} {what}." if ok else f"Bei {_name(target)} gibt es nichts zu tun.",
                None, None)

    if cmd in CURRENCY_CMDS and num is not None:
        # at once: the amount comes as a mail attachment (claim in the mailbox, no restart needed)
        shown = " ".join(parts)
        if notify(target, f"{shown}: im Anhang – einfach abholen.", [(CURRENCY_CMDS[cmd], num)]):
            if target == str(gid):
                return f"{code!r} sent as mail to {target}", None, None, None
            return (f"{code!r} sent as mail to {target}", f"{shown} an {_name(target)} geschickt (Post).",
                    None, None)
        action = {"currency": {str(CURRENCY_CMDS[cmd]): num}}  # no mailbox: set it at the next login
    elif cmd == "/level" and num is not None:
        action = {"level": num}
    elif cmd == "/items" and num is not None:
        action = {"items": num}
    elif cmd == "/allcards":
        action = {"all_cards": True}
    elif cmd == "/card" and args:
        confs = find_cards(" ".join(args))
        if not confs:
            return (f"/card: no card {' '.join(args)!r}",
                    f"Keine Karte mit „{' '.join(args)}“ gefunden (Name wie im Spiel, z. B. /card Aone).",
                    None, None)
        action = {"cards": confs}
    elif cmd == "/potential" and args:
        suit = find_suit(" ".join(args))
        if suit is None:
            return (f"/potential: no set {' '.join(args)!r}",
                    "Set nicht eindeutig gefunden. Sets: " + ", ".join(sorted(suit_names().values())) + ".",
                    None, None)
        action = {"specialties": full_set(suit)}
    elif cmd == "/maxcards":
        action = {"max_cards": True}
    elif cmd == "/infinite" and args and args[0].lower() in ("on", "an", "off", "aus"):
        action = {"flags": {"infinite_stamina": args[0].lower() in ("on", "an")}}
    elif cmd == "/power" and args and (num is not None or args[0].lower() in ("off", "aus")):
        action = {"power": {"*": None if num is None else {"pct": num}}}
    else:
        return f"unknown command {code!r}", f"Unbekannter Befehl: {code}. {HELP}", None, None
    queue(target, [action])
    shown = " ".join(parts)
    if target == str(gid):
        return (f"{code!r} queued for {target}",
                f"{shown} ist vorgemerkt und wirkt beim nächsten Login – Spiel einmal neu starten.", None, None)
    return (f"{code!r} queued for {target}", f"{shown} für {_name(target)} vorgemerkt.", target,
            f"Ein Admin hat dir {shown} gegeben. Spiel einmal neu starten, dann ist es da.")


async def handle_gift_code(client, pb_data: bytes):
    """0x2403 SettingGiftCodeC2S{1=code}: admin password or "/command" for admins (result as mail)."""
    code = wire.get(wire.parse(pb_data), 1, b"")
    code = (code.decode("utf-8", errors="replace") if isinstance(code, bytes) else str(code)).strip()
    gid = getattr(client, "gid", None)
    if enabled() and gid and code.startswith("/"):
        if is_admin(gid):
            line, msg, target, target_msg = command(gid, code)
            log.info(f"  ADMIN command from {gid}: {line}")
            if msg:
                notify(gid, msg)
            if target and target_msg and str(target) != str(gid):
                notify(target, target_msg)
        else:
            log.info(f"  ADMIN command {code!r} from {gid} ignored (no admin rights)")
            notify(gid, "Für Admin-Befehle fehlen dir die Rechte auf diesem Server.")
    elif enabled() and gid and check_password(code):
        if multiplayer():
            st = admins_state()
            if not st.get("owner"):
                st["owner"] = str(gid)
                save_admins(st)
                log.info(f"  ADMIN owner of this shared server: {gid}")
                notify(gid, "Du bist jetzt Besitzer dieses Servers und hast Admin-Rechte. " + HELP)
            else:
                log.info(f"  admin password from {gid} ignored (owner is {st['owner']})")
        else:
            state = unlocked()
            state[str(gid)] = time.time()
            ACCOUNTS.mkdir(exist_ok=True)
            UNLOCKED.write_text(json.dumps(state, indent=1), encoding="utf-8")
            log.info(f"  ADMIN MODE unlocked for {gid}")
            notify(gid, "Admin Mode freigeschaltet: im Launcher gibt es jetzt den Knopf „Admin Mode“. " + HELP)
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
