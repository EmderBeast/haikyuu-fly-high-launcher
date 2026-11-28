"""Card power (rating) as the client works it out, and the card-level stat shares it counts.

Belegt (client decoded/analysis/hotfix_il_full.txt, checked on the captured rooms):
  TeamPlayerCalculator::CalcPower: power = (stat part + skill part) // 10000
  - stat part (GetPowerAttrList / GetAttrPowerFactor): only the card's own attack (Quick Attack 0 when
    playerlist.AttackType is 2, else Power Attack 1), stats 2-6 and the crit / bonus stats 9-14, each
    (val + extrval) x propertypowerfactor (PropertyID, Star range, LvRange); morale / stamina count 0
  - skill part, with the skill factor skillpowerfactor (Quality = Rare, Star, LvRange; 10000 without a row):
    skilllist.SkillPower of the card's skills x factor; training skills (skilltemplate skill with
    Unlockcondition "2,x"): constant 10128 by rare (50 ... 300) x 10000; resonance ResonancePower x factor;
    the memory's recallingskill(SkillId*10 + stage).Power x factor when the memory's job is the card's;
    potential sets with NumB pieces: SkillBPower x factor
  - card-level stat shares (AddExtAttr: 2xx = percent of val of stat xx, else flat): training steps,
    collection bonds (trammel Type 1, all characters recorded in 0x0408, level = lowest record star - 1,
    trammelproperty row of the card's character), gallery rows of RewardType 1 among collect_awarded
    for the card's job
  Team shares (cheers, school bonds, coach) are not part of a card's power.
Checked: the captured account's cards as Garena sent them (val / extrval from the room) - test_power.py.
The +150 that 9 captured cards showed was the Reflex base value (attribute.json DefCritRate 500, stat 12),
now read in matches.card_attrs; left: 3 card states 90 / 108 below Garena.
"""
import gameconfig
import json

SCALE = 10000
CRIT_STATS = (9, 10, 11, 12, 13, 14)


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


def _flat(v):
    out = []
    for x in v if isinstance(v, list) else ([] if v is None else [v]):
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def _num(v, d=0):
    return v if isinstance(v, int) and v != -2147483648 else d


PPF = _load("propertypowerfactor")
SPF = _load("skillpowerfactor")
SKILLLIST = {r["ID"]: r for r in _load("skilllist")}
TEMPLATE = {r["ID"]: r for r in _load("skilltemplate")}
RECALLING = {r["ID"]: r for r in _load("recalling")}
REC_SKILL = {r["ID"]: r for r in _load("recallingskill")}
SPECIALTY = {r["ID"]: r for r in _load("specialty")}
SUITS = {r["ID"]: r for r in _load("specialtysuitconf")}
TRAMMEL = [r for r in _load("trammel") if r.get("Type") == 1]
TPROP = {(r["TrammelID"], _num(r.get("CharacterPropertyID")), r["TrammelLevel"]): r for r in _load("trammelproperty")}
GALLERY = {r["ID"]: r for r in _load("playergalleryrewardconf")}
_c = {r["ID"]: r for r in _load("constant")}.get(10128, {})
TRAIN_POWER = [int(x) for x in str(_c.get("Data") or "50,100,150,200,250,300").split(",") if x.strip()]


def _in(rng, x):
    rng = _flat(rng)
    return len(rng) >= 2 and rng[0] <= x <= rng[1]


def attr_factor(t: int, star: int, level: int) -> int:
    for r in PPF:
        if _num(r.get("PropertyID")) == t and _in(r.get("Star"), star) and _in(r.get("LvRange"), level):
            return _num(r.get("PowerFactor"))
    return 0


def skill_factor(rare: int, star: int, level: int) -> int:
    for r in SPF:
        if _num(r.get("Quality")) == rare and _in(r.get("Star"), star) and _in(r.get("LvRange"), level):
            return _num(r.get("PowerFactor"), SCALE)
    return SCALE


def power_stats(conf: int) -> list[int]:
    attack = 0 if (gameconfig.PLAYERS.get(conf) or {}).get("AttackType") == 2 else 1
    return [attack, 2, 3, 4, 5, 6, *CRIT_STATS]


def skill_part(entry) -> int:
    card = entry.info
    row = gameconfig.PLAYERS.get(card.conf_id) or {}
    rare = _num(row.get("Rare"), 1)
    f = skill_factor(rare, card.star, card.level)
    total = sum(_num(SKILLLIST.get(s, {}).get("SkillPower")) for s in card.skills if s) * f
    tpl = TEMPLATE.get(row.get("Skilltemplate"), {})
    for s in card.skills:
        sid = next((_num(tpl.get(k)) for k in ("Skill1", "Skill2", "Skill3", "Skill4")
                    if _num(tpl.get(k)) and _num(tpl.get(k)) // 10 == s // 10), 0)
        cond = str(SKILLLIST.get(sid, {}).get("Unlockcondition", "")).split(",")
        if len(cond) == 2 and cond[0] == "2" and 0 < rare <= len(TRAIN_POWER):
            total += TRAIN_POWER[rare - 1] * SCALE
    for res in gameconfig.resonance_rows(card.conf_id):
        if _num(res.get("TotalLevel"), 99) <= card.skill_resonance_unlock_level:
            rp = _flat(res.get("ResonancePower"))
            if len(rp) >= 4 and rp[1] == 1:
                total += rp[3] * f
    if entry.HasField("recalling_entity") and entry.recalling_entity.conf_id:
        mem = RECALLING.get(entry.recalling_entity.conf_id)
        if mem and mem.get("Job") == row.get("Job"):
            total += _num(REC_SKILL.get(mem["SkillId"] * 10 + entry.recalling_entity.stage, {}).get("Power")) * f
    suits: dict[int, int] = {}
    for s in entry.specialtys_entity:
        sid = SPECIALTY.get(s.conf_id, {}).get("SuitID")
        if sid:
            suits[sid] = suits.get(sid, 0) + 1
    for sid, n in suits.items():
        suit = SUITS.get(sid, {})
        if n >= _num(suit.get("NumB"), 99):
            total += _num(suit.get("SkillBPower")) * f
    return total


def card_power(entry, shares: dict | None = None) -> int:
    """entry: PlayerCardInfo; shares = team extrval to leave out (stat -> value)."""
    card = entry.info
    vals = {a.type: a.val + a.extrval - (shares or {}).get(a.type, 0) for a in entry.attr}
    stat = sum(vals.get(t, 0) * attr_factor(t, card.star, card.level) for t in power_stats(card.conf_id))
    return (stat + skill_part(entry)) // SCALE


# ------------------------------------------------------------- card-level shares (AddExtAttr)
def add_ext(extra: dict, base: dict, attr: int, value: int):
    if attr >= 200:
        extra[attr - 200] = extra.get(attr - 200, 0) + base.get(attr - 200, 0) * value // SCALE
    else:
        extra[attr] = extra.get(attr, 0) + value


def _kv(v):
    out, key = [], 0
    flat = _flat(v)
    for tag, val in zip(flat[::2], flat[1::2]):
        if tag == 8:
            key = val
        elif tag == 16:
            out.append((key, val))
            key = 0
    return out


def collection_bonds(conf: int, base: dict, records: dict[int, int]) -> dict:
    """Collection bonds (trammel Type 1) of the card's character: all members recorded, level = lowest
    record star - 1 (FetterClient::get_Level / GetFetterLevel)."""
    extra = {}
    for t in TRAMMEL:
        chars = _flat(t.get("CharacterID"))
        if conf % 10000 not in chars or not all(c in records for c in chars):
            continue
        level = min(records[c] for c in chars) - 1
        row = TPROP.get((t["ID"], conf % 10000, level))
        for k, v in _kv(row.get("Property")) if row else []:
            add_ext(extra, base, k, v)
    return extra


def gallery_shares(conf: int, base: dict, awarded) -> dict:
    """Gallery rows of RewardType 1 the account has claimed, for the card's job ([8 job, 16 attr, 24 value])."""
    extra = {}
    job = (gameconfig.PLAYERS.get(conf) or {}).get("Job")
    for i in awarded:
        row = GALLERY.get(i)
        if not row or row.get("RewardType") != 1:
            continue
        flat = _flat(row.get("GalleryReward"))
        cur = None
        for tag, val in zip(flat[::2], flat[1::2]):
            if tag == 8:
                cur = [val, 0, 0]
                if cur[0] == job:
                    pass
            elif cur is not None and tag == 16:
                cur[1] = val
            elif cur is not None and tag == 24:
                cur[2] = val
                if cur[0] == job:
                    add_ext(extra, base, cur[1], cur[2])
    return extra


def account_context(acc) -> tuple[dict, list]:
    """(character -> best recorded star, gallery rows claimed) of an account."""
    import wire
    records = {}
    for num, _, v in wire.parse(acc.statistics()):
        if num == 2:
            f = wire.parse(v)
            records[wire.get(f, 1, 0)] = wire.get(f, 2, 0)
    try:
        import gallery
        awarded = list(gallery.state(acc).collect_awarded)
    except Exception:
        awarded = []
    return records, awarded


# ------------------------------------------------------------- team power (TeamCalculator::CalcPower)
TRAMMEL_ALL = {r["ID"]: r for r in _load("trammel")}
TPROP_ID = {r["ID"]: r for r in _load("trammelproperty")}
COACHES = {r["ID"]: r for r in _load("coachlist")}


def share_power(team, share: dict) -> int:
    """Power of the team shares (stat -> extrval on every player: cheers, coach) over the first 7 cards."""
    total = 0
    for p in list(team.formation.players)[:7]:
        if p.info.conf_id:
            total += sum(share.get(t, 0) * attr_factor(t, p.info.star, p.info.level)
                         for t in power_stats(p.info.conf_id))
    return total // SCALE


def bond_power(team) -> int:
    """Battle bonds whose CharacterNumber first members all stand in the first 7: average skill factor of
    the members x TrammeIsStrength[level - 1] (GetFetterPlayerPower)."""
    first7 = [p for p in list(team.formation.players)[:7] if p.info.conf_id]
    here = {p.info.conf_id % 10000: p for p in first7}
    total, seen = 0, set()
    for fid in team.formation.fetter_id_levels:
        row = TPROP_ID.get(fid)
        bond = TRAMMEL_ALL.get(row["TrammelID"]) if row else None
        if not bond or bond.get("Type") != 2 or bond["ID"] in seen:
            continue
        chars = _flat(bond.get("CharacterID"))
        need = chars[:max(1, _num(bond.get("CharacterNumber")) or len(chars))]
        strength = _flat(bond.get("TrammeIsStrength"))
        level = row["TrammelLevel"]
        if not all(c in here for c in need) or not 0 < level <= len(strength):
            continue
        seen.add(bond["ID"])
        f = [skill_factor(_num((gameconfig.PLAYERS.get(here[c].info.conf_id) or {}).get("Rare"), 1),
                          here[c].info.star, here[c].info.level) if c in here else 0 for c in chars]
        total += sum(f) // len(chars) * strength[level - 1]
    return total // SCALE


def coach_power(team) -> int:
    """coachlist.ForceNumber + coachbuff.ForceNumber of its job buffs (GetCoachActiveBuffPower)."""
    if not team.formation.HasField("coach_card") or not team.formation.coach_card.conf_id:
        return 0
    import matches
    card = team.formation.coach_card
    return _num(COACHES.get(card.conf_id, {}).get("ForceNumber")) + matches.coach_force(card)


def team_power(team, share: dict) -> int:
    return (sum(p.power for p in list(team.formation.players)[:7]) + share_power(team, share)
            + bond_power(team) + coach_power(team))
