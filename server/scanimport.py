"""
Admin Mode: load a scan of the HFH-Bot (the file the bot makes for the CC simulator / Fly High Hub,
{"hfhscan": 1, ...}) into an account on this server, so it has the scanned account's players, potentials,
memories and coaches. Queued like every Admin Mode change ({"scan": <file>}) and applied at the next login.

What the file holds (HFH-Bot README, scans in HFH-Bot/scans) and how it is mapped (game tables):
  - players [{id (playerlist id), lv, stars, skills {S1..S4: level}, memory {id, tier}, pots [...]}]:
    the account's card of that character (the one with the highest level, else a new card) gets level, stars,
    skill levels (skilllist id = card*1000 + slot*100 + level, from the card's skilltemplate ids), the memory
    and its 6 potentials; the potentials it wore before come off;
  - pots: potentials in the inventory, created free;
  - memoryCopies [{id, tier, lv, n}] (or memories {id: tier}): owned memories; the scan's memory id is the
    memory's skill (recalling.SkillId), tier = stage, lv = level;
  - coaches [{name, lv (rarity), level}]: coach cards (coachlist by English name and CoachRare);
  - a potential {slot I..VI, set (English set name, "EnH." = Enhancement), lv, main, rolls {stat: count}}:
    specialty row of that set and place at Legendary (Rare 4); main stat by name (Quick 0, Power 1, Set 2,
    Block 3, Receive 4, Serve 5, Save 6, Awareness 9, Strength 10, AtkTech 11, Reflex 12, Spirit 13, DefTech 14;
    for the seven skills the percent value where the place offers it); sub stats "<Stat>Flat" / "<Stat>Pct" /
    plain name, level = count - 1.
Not imported (the server has no such state): bonds, collections, cheers, the gallery value; the coaches'
Positional Advantages ("pas") - imported coaches draw them like a level-up does.
A second import replaces the potentials, memories and coaches of the first one (admin_flags "scan_import").
"""
import json
import re

import gameconfig
import wire

STATS = {"Quick": 0, "Power": 1, "Set": 2, "Block": 3, "Receive": 4, "Serve": 5, "Save": 6,
         "Awareness": 9, "Strength": 10, "AtkTech": 11, "Reflex": 12, "Spirit": 13, "DefTech": 14}
SLOTS = ["I", "II", "III", "IV", "V", "VI"]
LEGENDARY = 4
_cache = {}


def _cfg(name):
    if name not in _cache:
        try:
            _cache[name] = json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))
        except Exception:
            _cache[name] = []
    return _cache[name]


def _english(ref) -> str:
    import admin
    m = re.findall(r"\d+", str(ref or ""))
    row = admin._lang().get(int(m[0])) if m else None
    return (row.get("English") or "") if row else ""


def _norm(t: str) -> str:
    t = str(t or "").lower().replace("enh.", "enhancement")
    return "".join(ch for ch in t if ch.isalnum())


def suits() -> dict:
    if "suits" not in _cache:
        _cache["suits"] = {_norm(_english(r.get("Name"))): r["ID"] for r in _cfg("specialtysuitconf")}
    return _cache["suits"]


def memory_conf(scan_id) -> int | None:
    """The scan's memory id is the memory's skill (recalling.SkillId)."""
    if "mems" not in _cache:
        _cache["mems"] = {r.get("SkillId"): r["ID"] for r in _cfg("recalling")}
    return _cache["mems"].get(int(scan_id)) if str(scan_id).isdigit() else None


def coach_conf(name, rare) -> int | None:
    if "coaches" not in _cache:
        _cache["coaches"] = {(_norm(_english(r.get("CoachName"))), r.get("CoachRare")): r["ID"] for r in _cfg("coachlist")}
    return _cache["coaches"].get((_norm(name), int(rare or 0)))


def _attr_of(key: str, main_id: dict, vice_id: dict, vice: bool):
    """'SetPct' / 'SetFlat' / 'Strength' -> potential attr id."""
    pct = key.endswith("Pct")
    name = key[:-3] if pct else key[:-4] if key.endswith("Flat") else key
    p = STATS.get(name)
    if p is None:
        return None
    return (vice_id if vice else main_id).get(200 + p if pct else p)


def potential(q: dict) -> dict | None:
    """A scanned piece -> {"conf", "position", "level", "main", "vices"} (make_specialty arguments)."""
    import admin
    try:
        pos = SLOTS.index(q.get("slot"))
    except ValueError:
        return None
    suit = suits().get(_norm(q.get("set")))
    rows = [r for r in admin._specialty_rows().values()
            if r["SuitID"] == suit and (r.get("Position") or 0) == pos]
    if not rows:
        return None
    row = next((r for r in rows if r["Rare"] == LEGENDARY), max(rows, key=lambda r: r["Rare"]))
    main_id, vice_id = admin._attr_ids()
    options = admin._listify(row["MainattrID"])
    main = options[0]
    p = STATS.get(str(q.get("main") or ""))
    if p is not None:
        for a in ((200 + p, p) if p <= 6 else (p,)):
            if main_id.get(a) in options:
                main = main_id[a]
                break
    vices = []
    for key, count in (q.get("rolls") or {}).items():
        v = _attr_of(key, main_id, vice_id, True)
        if v is not None:
            vices.append([v, max(0, int(count) - 1)])
    level = max(0, min(12, int(q.get("lv") or 0)))
    return {"conf": row["ID"], "position": pos, "level": level, "main": main, "vices": vices[:4]}


def _skills(conf: int, levels: dict) -> list[int] | None:
    tpl = gameconfig.SKILLS.get(conf, {}) if gameconfig.AVAILABLE else {}
    out = []
    for slot, key in enumerate(("Skill1", "Skill2", "Skill3", "Skill4"), 1):
        base = tpl.get(key)
        if not isinstance(base, int) or base <= 0:
            continue
        lv = max(1, int(levels.get(f"S{slot}") or 1))
        sid = base + lv - 1
        while sid > base and sid not in gameconfig.SKILL_LIST:
            sid -= 1
        out.append(sid)
    return out or None


def _forget_previous(acc, state: dict):
    """Take the potentials, memories and coaches of the last import out again (cards keep their values)."""
    import admin
    import coach
    for sid in state.get("specs", []):
        acc.specialties.pop(int(sid), None)
    for rid in state.get("recs", []):
        acc.recallings.pop(int(rid), None)
    st = coach._store(acc.gid)
    for cid in state.get("coaches", []):
        st["cards"].pop(str(cid), None)
    gone_specs = {int(s) for s in state.get("specs", [])}
    gone_recs = {int(r) for r in state.get("recs", [])}
    if not gone_specs and not gone_recs:
        return
    fixed = {}
    for inst, card in admin._all_cards(acc).items():
        new = card
        slots = admin._slots(card)
        if any(s in gone_specs for s in slots):
            new = admin._with_slots(new, [0 if s in gone_specs else s for s in slots])
        f = wire.parse(new)
        if wire.get(f, 12, 0) in gone_recs:
            new = wire.build([x for x in f if x[0] != 12])
        if new != card:
            fixed[inst] = new
    admin._put_cards(acc, fixed)


def apply(acc, scan: dict) -> list[str]:
    """Load the scan into the account; returns the report lines."""
    import admin
    import coach
    import matches
    if not isinstance(scan, dict) or int(scan.get("hfhscan") or 0) != 1:
        return ["Scan: keine Scan-Datei des HFH-Bots"]
    # the perfect-potentials switch would put its sets back over the scanned pieces: off, its sets gone
    if acc.admin_flags.get(admin.PERFECT) or acc.admin_flags.get("perfect"):
        made = {int(i) for ids in (acc.admin_flags.get("perfect") or {}).get("made", {}).values() for i in ids}
        for sid in made:
            acc.specialties.pop(sid, None)
        acc.admin_flags.pop("perfect", None)
        acc.admin_flags[admin.PERFECT] = False
    state = acc.admin_flags.get("scan_import") or {}
    _forget_previous(acc, state)
    new_state = {"specs": [], "recs": [], "coaches": []}
    report = []

    def make_spec(p, owner=0):
        sid = admin.SPECIALTY_INST_BASE + acc.next_inst
        acc.next_inst += 1
        acc.put_specialty(admin.make_specialty(sid, p["conf"], p["level"], p["main"], p["vices"]))
        if owner:
            admin._set_owner(acc, sid, owner)
        new_state["specs"].append(sid)
        return sid

    # memories: the scanned copies, those the players wear first
    copies = []
    for m in scan.get("memoryCopies") or []:
        copies += [(m.get("id"), int(m.get("tier") or 1), int(m.get("lv") or 1))] * max(1, int(m.get("n") or 1))
    if not copies and isinstance(scan.get("memories"), dict):
        copies = [(k, int(v or 1), 1) for k, v in scan["memories"].items()]

    def make_rec(scan_id, tier, level):
        conf = memory_conf(scan_id)
        if conf is None:
            return None
        level = max(1, min(admin.MEM_MAX_LEVEL, level))
        raw = acc.add_recalling(conf, level, admin.MEM_MAX_LEVEL, max(1, min(admin.MEM_MAX_STAGE, tier)))
        rid = wire.get(wire.parse(raw), 1)
        new_state["recs"].append(rid)
        return rid

    cards = admin._all_cards(acc)
    by_conf = {}
    for inst, card in cards.items():
        f = wire.parse(card)
        conf, level = wire.get(f, 3, 0), wire.get(f, 4, 0)
        if conf not in by_conf or level > by_conf[conf][1]:
            by_conf[conf] = (inst, level)
    changed, n_players, n_pots, missing = {}, 0, 0, []
    for x in scan.get("players") or []:
        conf = int(x.get("id") or 0)
        if conf not in gameconfig.PLAYERS:
            missing.append(str(conf))
            continue
        if conf in by_conf:
            inst = by_conf[conf][0]
            card = changed.get(inst) or cards[inst]
        else:
            inst, card = acc.add_card(conf)
            by_conf[conf] = (inst, 1)
        f = wire.parse(card)
        if int(x.get("stars") or 0) > 0:
            f = wire.set_varint(f, 6, max(1, min(7, int(x["stars"]))))
        if int(x.get("lv") or 0) > 0:
            star = wire.get(f, 6, 1)
            f = wire.set_varint(f, 4, max(1, min(int(x["lv"]), gameconfig.level_cap(star))))
            f = [y for y in f if y[0] != 5]  # no leftover EXP
        sk = _skills(conf, x.get("skills") or {}) if x.get("skills") else None
        if sk:
            f = [y for y in f if y[0] != 8] + [(8, 2, b"".join(wire.varint(s) for s in sk))]
        card = wire.build(sorted(f, key=lambda y: y[0]))
        mem = x.get("memory")
        if isinstance(mem, dict) and mem.get("id"):
            tier = int(mem.get("tier") or 1)
            pick = next((c for c in copies if str(c[0]) == str(mem["id"]) and c[1] == tier), None)
            if pick:
                copies.remove(pick)
            rid = make_rec(mem["id"], tier, pick[2] if pick else admin.MEM_MAX_LEVEL)
            if rid:
                acc.recallings[rid] = wire.build(wire.set_varint(wire.parse(acc.recallings[rid]), 8, inst))
                card = wire.build(wire.set_varint(wire.parse(card), 12, rid))
        if isinstance(x.get("pots"), list) and x["pots"]:
            for sid in admin._slots(card):
                if sid:
                    admin._set_owner(acc, sid, 0)
            slots = [0] * admin.SLOTS
            for q in x["pots"]:
                p = potential(q)
                if p:
                    slots[p["position"]] = make_spec(p, inst)
                    n_pots += 1
            card = admin._with_slots(card, slots)
        changed[inst] = card
        n_players += 1
    admin._put_cards(acc, changed)
    report.append(f"Scan: {n_players} Spieler, {n_pots} Potentials an Spielern"
                  + (f", unbekannt: {', '.join(missing)}" if missing else ""))
    free = 0
    for q in scan.get("pots") or []:
        p = potential(q)
        if p:
            make_spec(p)
            free += 1
    if scan.get("pots") is not None:
        report.append(f"Scan: {free} Potentials im Inventar")
    made = sum(1 for c in copies if make_rec(*c))
    if copies or new_state["recs"]:
        report.append(f"Scan: {len(new_state['recs'])} Memories ({made} im Inventar)")
    if scan.get("coaches"):
        st = coach._store(acc.gid)
        have = coach.cards(acc.gid, acc)
        nxt = max(list(have) + [1559926981427148799]) + 1
        n = 0
        for c in scan["coaches"]:
            conf = coach_conf(c.get("name"), c.get("lv"))
            if conf is None:
                continue
            card = matches.P.CoachCard(inst_id=nxt, conf_id=conf, level=1)
            nxt += 1
            want = max(1, min(int(c.get("level") or 1), coach.MAX_LEVEL.get(coach._rare(card), 15)))
            while card.level < want:  # level up like the game, job buffs drawn at the buff levels
                cost = coach.LEVELS.get((coach._rare(card), card.level), {}).get("Experience") or 0
                if not coach.level_up(card, cost):
                    break
            st["cards"][str(card.inst_id)] = card.SerializeToString().hex()
            new_state["coaches"].append(card.inst_id)
            n += 1
        report.append(f"Scan: {n} Coaches")
    skipped = [k for k in ("bonds", "collections", "cheers", "gallery") if k in scan]
    if skipped:
        report.append("Scan: nicht übernommen (gibt es auf dem Server nicht): " + ", ".join(skipped))
    acc.admin_flags["scan_import"] = new_state
    import pvp
    pvp.save()  # the coaches live in pvp.json
    return report
