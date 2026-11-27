"""
Match rooms (PVPCreateS2C) and results (PVPResultS2C).

The real game simulated rallies on Garena's servers and streamed them as frames
(PVPMatchData / PVPHandleServe). The client only simulates on its own for preset
matches (PVPRoomInfo.preset_match_id -> presetmatchconf / presetroundconf, scripted
rallies with a fixed final score), which is how the first Training Camp stages run.

Rooms are built from the captured room as a template:
  home = the player's own line-up (cards with stats from the attribute table)
  away = the stage's monster team (monsterteam + stage MonsterLevel/MonsterStar/ratios)

Card stats: attribute row (playerlist.Attribute // 10 * 10 + star) gives base and
per-level growth: base + growth * level, times the stage ratio (x/10000) and the stage
LevelDown (x/10000) for monsters. This reproduces the captured opponent's stats.
Own cards add their training steps (train.Trainattribute); specialties, coach, bonds
and other bonuses are not modelled.
"""
import json
import random
import sys
import time
from pathlib import Path

import gameconfig
import wire

sys.path.insert(0, str(Path(__file__).parent / "proto_gen"))
try:
    import haikyuu_pb2 as P  # noqa: E402
except ImportError:  # proto_gen is generated (gitignored): build it once
    import subprocess
    subprocess.run([sys.executable, str(Path(__file__).parent / "build_proto.py")], check=True)
    import haikyuu_pb2 as P  # noqa: E402

_REPLIES = json.loads((Path(__file__).parent / "data" / "replies.json").read_text(encoding="utf-8"))
import edition  # noqa: E402
_CFG = edition.CFG


def _table(name):
    return {r["ID"]: r for r in json.loads((_CFG / f"{name}.json").read_text(encoding="utf-8"))}


if gameconfig.AVAILABLE:
    ATTRIBUTES = _table("attribute")
    MONSTER_TEAMS = _table("monsterteam")
    PRESETS = _table("presetmatchconf")

# attribute type -> (attribute column, stage ratio column)
STATS = [
    (P.ATTR_FAST_ATTACK, "Fastattack", "FastattackRatio"),
    (P.ATTR_STRONG_ATTACK, "Strongattack", "StrongattackRatio"),
    (P.ATTR_PASS, "Pass", "PassRatio"),
    (P.ATTR_BLOCK, "Block", "BlockRatio"),
    (P.ATTR_DIG, "Dig", "DigRatio"),
    (P.ATTR_SERVE, "Serve", "ServeRatio"),
    (P.ATTR_SAVE, "Save", "SaveRatio"),
]
EXTRA = [(P.ATTR_MORALE, None), (P.ATTR_ENERGY, "Energy"), (P.ATTR_ATK_CRIT_RATE, "AtkCritRate"),
         (P.ATTR_ATK_CRIT_DMG, None), (P.ATTR_ATK_BONUS, None), (P.ATTR_DEF_CRIT_RATE, None),
         (P.ATTR_DEF_CRIT_HIT, None), (P.ATTR_DEF_BONUS, None)]
SCALE = 10000
COACH_STATS = True  # the coach's stats and skills follow the team's coach (switch_coach)
TEAM_SIZE = 13  # 7 on court + 6 bench slots
HOME_WIN = 1


def _num(v, default=0):
    return v if isinstance(v, int) and v != -2147483648 else default


def _captured(msg_id: str, cls):
    m = _REPLIES[msg_id]
    o = cls()
    o.ParseFromString(wire.body_to_pb(bytes.fromhex(m["body_hex"]), m["flags"]))
    return o


CONTEST_STAGE_BASE = 100000  # Extreme Training sends its floor (601), the stage row is 100601


def stage_of(args: P.PVPRoomArgs) -> int:
    """Stage row of a match request. Practice / Training Camp / Specialty Trial / club boss send the
    stage id itself, Extreme Training (FIGHT_CONTEST) its floor number.

    The client builds pvp_args in TeamView.FastFormationViewLogic::StartMatch: [line-up, stage], for
    practice [line-up, stage, double cost] (Garena captures: practice [1, 210116, 0], Extreme Training
    [2, 601]). Its third form [line-up, fight flag, stage] (stage state < 0, any type but practice) is
    known from the client code only, no capture."""
    a = args.pvp_args
    raw = a[1] if len(a) > 1 else 0
    if len(a) == 3 and args.pvp_type != P.FIGHT_SOURCE:
        raw = a[2]
    if args.pvp_type == P.FIGHT_CONTEST and raw and raw < CONTEST_STAGE_BASE:
        return CONTEST_STAGE_BASE + raw
    return raw


LINEUP_IN_REQUEST = (P.FIGHT_TOKYO, P.FIGHT_SOURCE, P.FIGHT_CONTEST, P.FIGHT_SPECIFIC_TRIAL)


def request_lineup(args: P.PVPRoomArgs) -> int | None:
    """Line-up the player picked in the formation view for this match (pvp_args[0], see stage_of).
    None for modes whose first argument means something else."""
    if args.pvp_type in LINEUP_IN_REQUEST and len(args.pvp_args) > 1:
        return args.pvp_args[0]
    return None


def double_cost(args: P.PVPRoomArgs) -> bool:
    """Practice match played at double cost (third request argument, client: PVEStageData.useDoubleCost)."""
    return args.pvp_type == P.FIGHT_SOURCE and len(args.pvp_args) > 2 and bool(args.pvp_args[2])


def stage_row(stage_id: int) -> dict:
    return gameconfig.STAGES.get(stage_id, {}) if gameconfig.AVAILABLE else {}


def preset_for(stage_id: int) -> int:
    return _num(stage_row(stage_id).get("PresetMatch"))


# --- card stats -----------------------------------------------------------
def _attr_row(conf_id: int, star: int) -> dict:
    base = _num(gameconfig.PLAYERS.get(conf_id, {}).get("Attribute"))
    return ATTRIBUTES.get(base // 10 * 10 + star) or ATTRIBUTES.get(base) or {}


def _train_bonus(conf_id: int, train: int) -> dict:
    """{attr type: bonus} from the training steps done (train ID = card * 10 + step)."""
    out = {}
    for step in range(1, train + 1):
        for t, v in gameconfig.parse_rewards(gameconfig.TRAIN.get(conf_id * 10 + step, {}).get("Trainattribute")):
            out[t] = out.get(t, 0) + v
    return out


def card_attrs(conf_id: int, level: int, star: int, train: int = 0, stage: dict | None = None) -> list:
    row = _attr_row(conf_id, star)
    bonus = _train_bonus(conf_id, train)
    level_down = _num((stage or {}).get("LevelDown"), SCALE) if stage else SCALE
    out = []
    for t, col, ratio_col in STATS:
        val = _num(row.get(col)) + _num(row.get(col + "upgrade")) * level + bonus.get(t, 0)
        if stage:
            val = val * _num(stage.get(ratio_col), SCALE) // SCALE * level_down // SCALE
        out.append(P.PlayerCardAttr(type=t, val=val, scaled=SCALE))
    for t, col in EXTRA:
        val = _num(row.get(col)) if col else 0
        if stage and t == P.ATTR_ENERGY:
            val = val * _num(stage.get("EnergyRatio"), SCALE) // SCALE
        out.append(P.PlayerCardAttr(type=t, val=val, scaled=SCALE))
    return out


def card_power(attrs: list) -> int:
    """Sum of the 7 stats incl. extrval (Garena's power follows val + extrval: 0.86-1.06 of it on the
    captured cards; its crit power factors and the coach / bond share are not modelled)."""
    return sum(a.val + a.extrval for a in attrs if a.type in (s[0] for s in STATS)) // SCALE


def _extras(card: P.PlayerCard, recalling: bytes | None) -> list:
    """skill_extras like the Garena server sends them for a card with a memory: the unlocked resonance
    skills (level 0) and the memory's skills (recallingskill.SkillGroup) at the memory's stage."""
    rec = P.PlayerRecalling()
    rec.ParseFromString(recalling)
    out = []
    for row in gameconfig.resonance_rows(card.conf_id):
        if _num(row.get("TotalLevel"), 99) <= card.skill_resonance_unlock_level:
            add = row.get("AddSkill") or []
            out += [P.SkillTarget(skill_id=s, target=1) for s in (add if isinstance(add, list) else [add])]
    sid = (gameconfig._cached("recalling").get(rec.conf_id) or {}).get("SkillId")
    group = next((r.get("SkillGroup") or [] for r in gameconfig._cached("recallingskill").values()
                  if r.get("SkillId") == sid and r.get("Level") == max(1, rec.stage)), [])
    group = group if isinstance(group, list) else [group]
    out += [P.SkillTarget(skill_id=s, target=1, level=max(1, rec.stage)) for s in group]
    return out


# --- potentials and memory stats (client: PlayerSystem.PlayerAttributeUtility) ----------------------
# BuildPlayerAttribute = star/level (PlayerCardAttr.val) + training, skill resonance, memory, potentials, all
# into extrval. AddExtrAttr(attr, value): attr >= 200 adds value / 10000 of the star/level val of stat
# attr - 200, else value itself. Potentials (BuildSpecialtyAttr): main and every sub stat -> specialtyattrconf
# row (AttrID, Level = attr level, Rare = specialty rarity) -> AddExtrAttr(Attr, Value); then per set
# (SuitID) with >= NumA potentials AttrA, >= NumB AttrB (SkillB, a set skill, is not modelled). Memory
# (BuildRecallingAttr): recallingarrt row of recalling.AttrGroupId for the memory's level, every BaseAttr
# (attr, value) + level * UpAttr[i], only when recalling.Job == the player's job.
# Checked on the 13 cards of the captured home team (0x0302): their extrval minus training, resonance,
# memory and potentials leaves per stat the same team-wide amount (coach / bonds, not modelled) on most
# cards; the crit and bonus stats 9-14 come out exactly.
_STAT_TABLES = {}


def _stat_tables() -> dict:
    if not _STAT_TABLES:
        def rows(name):
            try:
                return json.loads((_CFG / f"{name}.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                return []
        _STAT_TABLES["attr"] = {(r["AttrID"], r.get("Level", 0), r["Rare"]): r for r in rows("specialtyattrconf")}
        _STAT_TABLES["spec"] = {r["ID"]: r for r in rows("specialty")}
        _STAT_TABLES["suit"] = {r["ID"]: r for r in rows("specialtysuitconf")}
        _STAT_TABLES["memory"] = {r["ID"]: r for r in rows("recalling")}
        groups = {}
        for r in rows("recallingarrt"):
            groups.setdefault(r.get("Group"), []).append(r)
        _STAT_TABLES["memory_attr"] = {g: sorted(v, key=lambda r: r.get("Level", 0)) for g, v in groups.items()}
    return _STAT_TABLES


def _kv_pairs(v) -> list[tuple[int, int]]:
    """Table pair lists [8, key, 16, value, ...]; a value without its key has key 0 (protobuf default)."""
    flat = []
    for x in v if isinstance(v, list) else []:
        flat += x if isinstance(x, list) else [x]
    out, key = [], 0
    for i in range(0, len(flat) - 1, 2):
        if flat[i] == 8:
            key = flat[i + 1]
        elif flat[i] == 16:
            out.append((key, flat[i + 1]))
            key = 0
    return out


def _add_extra(extra: dict, base: dict, attr: int, value: int):
    if attr >= 200:
        extra[attr - 200] = extra.get(attr - 200, 0) + base.get(attr - 200, 0) * value // SCALE
    else:
        extra[attr] = extra.get(attr, 0) + value


def potential_stats(base: dict, specialties: list) -> dict:
    """{stat: extra} of the equipped potentials (PlayerSpecialty) and their set bonuses."""
    t = _stat_tables()
    extra, suits = {}, {}
    for s in specialties:
        cfg = t["spec"].get(s.conf_id)
        if not cfg:
            continue
        for a in [s.main_attr] + list(s.vice_attrs):
            row = t["attr"].get((a.attr_id, a.attr_lev, cfg["Rare"]))
            if row:
                _add_extra(extra, base, row.get("Attr", 0), row["Value"])
        suits[cfg["SuitID"]] = suits.get(cfg["SuitID"], 0) + 1
    for suit, n in suits.items():
        row = t["suit"].get(suit, {})
        if n >= _num(row.get("NumA"), 99):
            for k, v in _kv_pairs(row.get("AttrA")):
                _add_extra(extra, base, k, v)
        if n >= _num(row.get("NumB"), 99):
            for k, v in _kv_pairs(row.get("AttrB")):
                _add_extra(extra, base, k, v)
    return extra


def memory_stats(base: dict, conf_id: int, memory) -> dict:
    """{stat: extra} of an equipped memory (PlayerRecalling), only on a player of the memory's job."""
    t = _stat_tables()
    cfg = t["memory"].get(memory.conf_id)
    if not cfg or cfg.get("Job") != gameconfig.PLAYERS.get(conf_id, {}).get("Job"):
        return {}
    pick = None
    for row in t["memory_attr"].get(cfg.get("AttrGroupId"), []):
        if memory.level >= row.get("Level", 0):
            pick = row
    extra = {}
    if pick:
        ups = pick.get("UpAttr") or []
        for i, (k, v) in enumerate(_kv_pairs(pick.get("BaseAttr"))):
            up = ups[i] if i < len(ups) and isinstance(ups[i], int) else 0
            _add_extra(extra, base, k, v + memory.level * up)
    return extra


def card_entry(card: P.PlayerCard, stage: dict | None = None, recalling: bytes | None = None,
               specialties: list | None = None) -> P.PlayerCardInfo:
    """A card's PlayerCardInfo as Garena builds it: val = star / level, extrval = training, skill resonance
    (percent of val), memory and potentials (`specialties`: the equipped PlayerSpecialty bytes)."""
    attrs = card_attrs(card.conf_id, card.level, card.star, 0, stage)
    base = {a.type: a.val for a in attrs}
    extra = dict(_train_bonus(card.conf_id, card.train)) if card.train else {}
    power_extra = 0
    if card.skill_resonance_unlock_level:  # skill resonance: +x % basic stats, extra power
        pct, power_extra = gameconfig.resonance_bonus(card.conf_id, card.skill_resonance_unlock_level)
        for t, v in pct.items():
            extra[t] = extra.get(t, 0) + base.get(t, 0) * v // SCALE
    rec = None
    if recalling:
        rec = P.PlayerRecalling()
        rec.ParseFromString(recalling)
        for t, v in memory_stats(base, card.conf_id, rec).items():
            extra[t] = extra.get(t, 0) + v
    specs = [P.PlayerSpecialty.FromString(s) for s in specialties or [] if s]
    for t, v in potential_stats(base, specs).items():
        extra[t] = extra.get(t, 0) + v
    for a in attrs:
        a.extrval = extra.get(a.type, 0)
    entry = P.PlayerCardInfo(info=card, attr=attrs, power=card_power(attrs) + power_extra)
    entry.specialtys_entity.extend(specs)
    if rec is not None:  # equipped memory: its entity and skills (plus resonance skills, as the client expects)
        entry.recalling_entity.CopyFrom(rec)
        entry.skill_extras.extend(_extras(card, recalling))
    return entry


def equipped_specialties(acc, card: P.PlayerCard) -> list:
    """The PlayerSpecialty bytes on a card (PlayerCard.specialtys = inst ids by place, 0 = empty)."""
    return [s for s in (acc.get_specialty(i) for i in card.specialtys if i) if s]


def with_specialties(real: P.PlayerCardInfo, specialties: list) -> P.PlayerCardInfo:
    """A captured card's real PlayerCardInfo (incl. coach / bonds) with other potentials: the old ones'
    share comes off extrval, the new ones' goes on."""
    entry = P.PlayerCardInfo()
    entry.CopyFrom(real)
    base = {a.type: a.val for a in entry.attr}
    old = potential_stats(base, list(real.specialtys_entity))
    specs = [P.PlayerSpecialty.FromString(s) for s in specialties if s]
    new = potential_stats(base, specs)
    for a in entry.attr:
        a.extrval += new.get(a.type, 0) - old.get(a.type, 0)
    # Garena's power stays, moved by the difference of the 7 stats
    entry.power += sum(new.get(t, 0) - old.get(t, 0) for t, _, _ in STATS) // SCALE
    del entry.specialtys_entity[:]
    entry.specialtys_entity.extend(specs)
    return entry


# --- teams ----------------------------------------------------------------
def _formation(acc, fight_type: int, lineup: int | None = None):
    """The line-up (TeamFormation) the account uses for this fight type: the one named in the match
    request if the account has it, else the one saved for the fight type."""
    team = P.TeamInfoS2C()
    team.ParseFromString(acc.team_info())
    forms = list(team.formations)
    asked = next((f for f in forms if f.lineup == lineup), None) if lineup is not None else None
    if asked is not None:
        return asked
    chosen = next((c.line_up_chosen for c in team.fight_line_up_chosens if c.fight_type == fight_type), None)
    return next((f for f in forms if f.lineup == chosen), forms[0] if forms else None)


def _lineup(acc, fight_type: int, lineup: int | None = None) -> list[int]:
    form = _formation(acc, fight_type, lineup)
    return list(form.inst_ids) if form else []


def home_team(template: P.PVPTeamInfo, acc, gid: int, name: str, fight_type: int,
              lineup: int | None = None) -> P.PVPTeamInfo:
    team = P.PVPTeamInfo()
    team.CopyFrom(template)  # tactics, morale, icons: kept from the captured room
    team.gamer_id = gid
    team.name = name
    del team.formation.players[:]
    del team.skill_extras[:]
    team.formation.ClearField("coach_card")
    del team.formation.fetter_id_levels[:]
    captured = {p.info.inst_id: p for p in template.formation.players}
    form = _formation(acc, fight_type, lineup)
    if form:  # "Preset Sub Position": who the libero replaces (none = he stays on the bench)
        team.formation.libero_replace_type = form.libero_replace_type
        team.formation.auto_replace = form.auto_replace
        if form.HasField("genre"):  # Lineup Specialty of this line-up (decides the Specialty Counter)
            team.formation.genre.CopyFrom(form.genre)
    for inst in _lineup(acc, fight_type, lineup)[:TEAM_SIZE]:
        raw = acc.get_card(inst)
        if not raw:
            continue
        card = P.PlayerCard()
        card.ParseFromString(raw)
        real = captured.get(inst)
        specs = equipped_specialties(acc, card)
        if real and _same_card(real.info, card):
            # unchanged since the capture: real stats incl. specialties, coach and bonds; other potentials
            # (e.g. Admin Mode's perfect sets) replace the old ones' share
            if list(real.info.specialtys) == list(card.specialtys):
                entry = P.PlayerCardInfo()
                entry.CopyFrom(real)
            else:
                entry = with_specialties(real, specs)
                entry.info.CopyFrom(card)
            team.formation.players.append(entry)
        else:
            rec = acc.all_recallings().get(card.recalling) if card.recalling else None
            team.formation.players.append(card_entry(card, recalling=rec, specialties=specs))
    team.power = sum(p.power for p in team.formation.players[:7])
    if any(p.info.inst_id in captured for p in team.formation.players):
        # the captured room's team-wide extras (coach, bonds) belong to these cards
        team.formation.coach_card.CopyFrom(template.formation.coach_card)
        team.formation.fetter_id_levels.extend(template.formation.fetter_id_levels)
        team.skill_extras.extend(template.skill_extras)
    old = P.CoachCard()
    old.CopyFrom(team.formation.coach_card) if team.formation.HasField("coach_card") else None
    had = team.formation.HasField("coach_card")
    own_coach(team, acc, gid, form)
    if COACH_STATS:
        switch_coach(team, old if had else None, set(captured))
    import admin  # Admin Mode: per-card power changes (no-op without any)
    admin.apply_power(acc, team)
    return team


def _kv_pairs(v) -> list[tuple[int, int]]:
    """A repeated KVPairInt column as the table dump writes it: protobuf tags 8 (key) and 16 (value)
    with their numbers, the first pair nested and without its key (0)."""
    flat, out, key = [], [], 0
    def walk(x):
        for y in x if isinstance(x, list) else [x]:
            walk(y) if isinstance(y, list) else flat.append(y)
    walk(v)
    it = iter(flat)
    for tag in it:
        val = next(it, None)
        if tag == 8:
            key = val
        elif tag == 16:
            out.append((key, val))
            key = 0
    return out


def coach_values(card) -> dict[int, int]:
    """Stats a coach gives every player of its team (client TeamView.TeamCalculator.AddCoachAttr):
    per coachlist.PropertyNumber pair (attr type, value) value + coachlevel.Property of the coach's
    level and rarity, added to the player's extrval (x10000). Type 7 goes to the team, not here.
    Captured rooms: coach 12 (rarity 4) level 15 gives 420000 + 1680000 per basic stat."""
    if card is None or not card.conf_id:
        return {}
    row = _table_cached("coachlist").get(card.conf_id)
    if not row:
        return {}
    lv = next((r for r in _table_cached("coachlevel").values()
               if r.get("Level") == card.level and r.get("CoachRare") == row.get("CoachRare")), {})
    extra = _num(lv.get("Property"))
    return {k: v + extra for k, v in _kv_pairs(row.get("PropertyNumber")) if k != 7}


def coach_skills(card) -> tuple[list[int], dict[int, list[int]]]:
    """(team skills, job -> skills) of a coach: coachlist.PropertyBuff (in Garena's rooms a team-wide
    skill_extra, target 106) and the skills of its job_buffs (coachbuff.CoachBuffId, per player of
    that job)."""
    if card is None or not card.conf_id:
        return [], {}
    row = _table_cached("coachlist").get(card.conf_id) or {}
    pb = row.get("PropertyBuff")
    team = [x for x in (pb if isinstance(pb, list) else [pb]) if _num(x)]
    jobs: dict[int, list[int]] = {}
    for jb in card.job_buffs:
        ids = (_table_cached("coachbuff").get(jb.buff_id) or {}).get("CoachBuffId")
        jobs.setdefault(jb.job, []).extend(x for x in (ids if isinstance(ids, list) else [ids]) if _num(x))
    return team, jobs


_TABLES = {}


def _table_cached(name: str) -> dict:
    if name not in _TABLES:
        try:
            _TABLES[name] = _table(name)
        except FileNotFoundError:
            _TABLES[name] = {}
    return _TABLES[name]


def switch_coach(team: P.PVPTeamInfo, old, captured_ids: set) -> None:
    """The team's coach is now team.formation.coach_card (old = the one whose share the captured cards'
    stats and skill_extras already hold, or None). Stats: every player gets the new coach's values,
    minus the old one's for captured cards. Skills: the old coach's PropertyBuff / job skills leave the
    extras, the new ones come in (players without extras get job skills from skills.py itself)."""
    new = team.formation.coach_card if team.formation.HasField("coach_card") else None
    if old is not None and new is not None and old.SerializeToString() == new.SerializeToString():
        return
    nv, ov = coach_values(new), coach_values(old)
    for p in team.formation.players:
        base = ov if p.info.inst_id in captured_ids else {}
        for a in p.attr:
            d = nv.get(a.type, 0) - base.get(a.type, 0)
            if d:
                a.extrval += d
    (o_team, o_jobs), (n_team, n_jobs) = coach_skills(old), coach_skills(new)
    keep = [e for e in team.skill_extras if e.skill_id not in o_team]
    del team.skill_extras[:]
    team.skill_extras.extend(keep)
    have = {e.skill_id for e in team.skill_extras}
    team.skill_extras.extend(P.SkillTarget(skill_id=s, target=106) for s in n_team if s not in have)
    for p in team.formation.players:
        if not p.skill_extras:
            continue  # skills.py gives job_buffs to players without captured extras
        job = _num((gameconfig.PLAYERS.get(p.info.conf_id) or {}).get("Job"))
        gone = set(o_jobs.get(job, []))
        keep = [e for e in p.skill_extras if e.skill_id not in gone]
        del p.skill_extras[:]
        p.skill_extras.extend(keep)
        p.skill_extras.extend(P.SkillTarget(skill_id=s, target=1) for s in n_jobs.get(job, []))


def own_coach(team: P.PVPTeamInfo, acc, gid: int, form) -> bool:
    """The line-up names its coach itself (TeamFormation.coach_card_inst_id, as in Garena's 0x0B01 / 0x0B03):
    that coach of the player (coach.py: levelled, built in Admin Mode, ...) goes into the match team. Kept as it
    is when the line-up names no coach, one the player does not have, or the captured room's own coach.
    skills.py turns coach_card.job_buffs into skills for teams without captured skill_extras."""
    inst = form.coach_card_inst_id if form else 0
    if not inst or (team.formation.HasField("coach_card") and team.formation.coach_card.inst_id == inst):
        return False
    import coach
    mine = coach.cards(gid, acc).get(inst)
    if mine is None:
        return False
    team.formation.coach_card.CopyFrom(mine)
    return True


def _same_card(a: P.PlayerCard, b: P.PlayerCard) -> bool:
    return ((a.conf_id, a.level, a.star, a.train, list(a.skills), a.skill_resonance_unlock_level, a.recalling)
            == (b.conf_id, b.level, b.star, b.train, list(b.skills), b.skill_resonance_unlock_level, b.recalling))


def away_team(template: P.PVPTeamInfo, stage_id: int, now: int) -> P.PVPTeamInfo:
    row = stage_row(stage_id)
    team = P.PVPTeamInfo()
    team.CopyFrom(template)
    del team.formation.players[:]
    team.gamer_id = -stage_id
    team.name = row.get("Name") or team.name
    head = row.get("Head") or []
    if len(head) >= 2:
        team.icon, team.icon_frame = head[0], head[1]
    monsters = MONSTER_TEAMS.get(_num(row.get("MonsterTeam")), {}).get("MonsterID") or []
    levels, stars = row.get("MonsterLevel") or [], row.get("MonsterStar") or []
    for i, conf in enumerate(monsters):
        skills = gameconfig.new_card_fields(conf)[1] if gameconfig.new_card_fields(conf) else []
        card = P.PlayerCard(inst_id=now * 1000 + i + 1, create_time=now // 1000, conf_id=conf,
                            level=levels[i] if i < len(levels) else 1,
                            star=stars[i] if i < len(stars) else 2, skills=skills)
        team.formation.players.append(card_entry(card, row))
    while len(team.formation.players) < TEAM_SIZE:
        team.formation.players.append(P.PlayerCardInfo())
    del team.tactics[:]
    attack, defense = (row.get("AttackTactics") or [1001, 1]), (row.get("DefenseTactics") or [2003, 1])
    team.tactics.add(tactic_id=attack[0], level=attack[1])
    team.tactics.add(tactic_id=defense[0], level=defense[1])
    team.chosen.attck_id, team.chosen.defense_id = attack[0], defense[0]
    team.power = _num(row.get("RecommendedForce")) or sum(p.power for p in team.formation.players[:7])
    return team


# --- room / result --------------------------------------------------------
# modes against a stage's monster team: Training Camp, Practice Match, Extreme Training, Specialty
# Trial, club boss (FIGHT_ARENA = PvP against other players' teams is not one of them)
PVE_TYPES = (P.FIGHT_TOKYO, P.FIGHT_SOURCE, P.FIGHT_CONTEST, P.FIGHT_SPECIFIC_TRIAL, P.FIGHT_ASSOC_BOSS)
TIMED_SCORE = 15  # stages to 20/30 points are 5-minute score races (time_end 300 in the captures)
TIME_LIMIT = 300
# Stand-in preset matches for stages without their own: scripted 3-0 wins and a 1-3 loss.
WIN_PRESETS = (111, 112)
LOSS_PRESET = 400101


LIVE_POINTS = 5  # points to win a live match (the captured PvE matches were best-of-1 to 5)


class Match:
    """A room the server built. Preset rooms have the outcome decided at creation; live rooms
    (self.live, server-streamed rallies) decide it point by point."""

    def __init__(self, info: P.PVPRoomInfo, won: bool, stars: int, live=None):
        self.info, self._won, self.stars, self.live = info, won, stars, live

    @property
    def won(self) -> bool:
        return self.live.won if self.live else self._won


def played_of(match) -> dict | None:
    """The rallies a live match sent (for build_result's statistics), None for scripted matches."""
    return getattr(match.live, "played", None) if match is not None and match.live else None


def win_chance(home_power: int, away_power: int) -> float:
    return min(0.95, max(0.05, 0.5 + (home_power / max(away_power, 1) - 1) * 1.5))


def point_chance(home_power: int, away_power: int) -> float:
    """Chance that home wins a single point (stage-1 live matches, until rallies are simulated)."""
    return min(0.8, max(0.2, 0.5 + (home_power / max(away_power, 1) - 1) * 0.6))


def decide(home_power: int, away_power: int) -> tuple[bool, int]:
    """Win chance and stars from the power ratio (the real rally simulation ran on Garena's servers)."""
    ratio = home_power / max(away_power, 1)
    won = random.random() < win_chance(home_power, away_power)
    stars = 0b111 if ratio >= 1.2 else 0b011 if ratio >= 1.0 else 0b001
    return won, stars if won else 0


def build_room(args_pb: bytes, acc, gid: int, name: str) -> tuple[bytes, Match] | None:
    """PVPCreateS2C for a PvE stage, or None for modes still answered from the capture."""
    import livematch  # imports this module
    args = P.PVPRoomArgs()
    args.ParseFromString(args_pb)
    stage_id = stage_of(args)
    row = stage_row(stage_id)
    if args.pvp_type not in PVE_TYPES or not row or not _num(row.get("MonsterTeam")):
        return None
    now = int(time.time() * 1000)
    create = _captured("0x0302", P.PVPCreateS2C)
    info = create.info
    info.room_args.CopyFrom(args)
    info.home.CopyFrom(home_team(info.home, acc, gid, name, args.pvp_type, request_lineup(args)))
    info.away.CopyFrom(away_team(info.away, stage_id, now))
    info.bo_type = _num(row.get("BoType"), info.bo_type)
    info.bo_score = _num(row.get("BoScore"), info.bo_score)
    preset = preset_for(stage_id)
    if preset:  # the stage's own scripted match
        p = PRESETS.get(preset, {})
        scores, stars = p.get("PresetScores") or [1, 0], p.get("PresetStars") or [1, 1, 1]
        won = scores[0] > scores[1]
        mask = sum(1 << i for i, s in enumerate(stars) if s) if won else 0
    elif livematch.LIBRARY:  # server-streamed rallies
        info.preset_match_id = 0
        info.bo_type = 1
        info.bo_score = _num(row.get("BoScore"), LIVE_POINTS) or LIVE_POINTS  # as Garena: 7 practice, 5 ...
        info.time_end = TIME_LIMIT if info.bo_score >= TIMED_SCORE else 0
        if info.time_end:  # Garena's timed rooms (club boss, Specialty Trial) also carry score_end 3
            info.score_end = 3
        info.first_serve_team = False
        info.create_time = now // 1000
        info.session = now
        info.seed = random.getrandbits(31)
        mask = decide(info.home.power, info.away.power)[1] or 0b001
        live = livematch.LiveMatch(info, point_chance(info.home.power, info.away.power))
        return create.SerializeToString(), Match(info, False, mask, live)
    else:
        won, mask = decide(info.home.power, info.away.power)
        preset = random.choice(WIN_PRESETS) if won else LOSS_PRESET
        scores = PRESETS.get(preset, {}).get("PresetScores") or [3, 0]
        info.bo_type, info.bo_score = 1, max(scores)  # the script decides the final score
    info.preset_match_id = preset
    info.create_time = now // 1000
    info.session = now
    info.seed = random.getrandbits(31)
    return create.SerializeToString(), Match(info, won, mask)


def roll_stage_rewards(stage_id: int, first_clear: bool) -> list[tuple[int, int]]:
    """'prob,item,amount|...' (prob in 1/10000) from stage Reward, plus FirstReward on the first clear."""
    row = stage_row(stage_id)
    out = []
    specs = ([row.get("FirstReward")] if first_clear else []) + [row.get("Reward")]
    for spec in specs:
        for part in (spec or "").split("|"):
            bits = part.split(",")
            if len(bits) == 3 and random.randrange(SCALE) < int(bits[0]):
                out.append((int(bits[1]), int(bits[2])))
    return gameconfig.open_rewards(out)


RECEIVE_FORMULAS = (7, 8)  # DigSpike / Save: the result lists them as 2 (receive) for the home team


def live_statistics(result, room: P.PVPRoomInfo, played: dict) -> None:
    """Fills PVPRoomResult.home/away from the rallies of a server-streamed match (as the client saw them),
    instead of the captured match's statistics. Rules checked against all 188 complete Garena matches of
    the captures (decrypted_*; decoded/analysis/frame_format.md 3.1):

      bo_statistics[0]  score = points won, win, round_num = rallies played
      player_statistics one per line-up card (inst_id), in line-up order
        formula_statistics  one per announced touch (round_data.action_data) of the player:
                            formula_type (7/8 -> 2 for the home team), hit_result, link, skill_id, crit,
                            frame_idx = global frame of the announcement (earlier rallies' last frame_index
                            summed), skill_win = the last touch of the team that won the point
        score_skills        skill_id of those point-winning touches
        serve_balls         the hand serve result (EHandServeResult) of every hand serve of the player
        on_stage            the player is in the last rally
        formidable_*        the last power_hit_ball_data of the match (count = attack + defence)
      score_act_statistics  per point won: own scorer's card (-1 without an own touch), the opponent who
                            touched last (card), own formula (-1), that opponent's player id ("rival_formula")
      morale_max 120, morale_num = rallies in which the team released its morale skill
    Not from the captures (cleared): attr, formidable_serve_score, tactic_num, physical_recovery_total."""
    acts, wins, last_ids, power = [], [], set(), {}
    released = {0: 0, 1: 0}
    g0, prev = 0, (0, 0)
    order = sorted(played)
    for n, key in enumerate(order):
        frames, hand = played[key]
        frames = [P.FrameData.FromString(b) if isinstance(b, (bytes, bytearray)) else b for b in frames]
        rally, winner, morale = [], None, set()
        for f in frames:
            if f.HasField("round_data") and f.round_data.HasField("action_data"):
                rally.append((g0 + f.frame_index, f.round_data.action_data))
            if f.HasField("game_data") and f.game_data.HasField("score"):
                sc = (f.game_data.score.host_score, f.game_data.score.guest_score)
                winner = 0 if sc[0] > prev[0] else 1
                prev = sc
            for t in f.team_data:
                if t.morale_skill.state == P.Released and t.morale_skill.skill_id:
                    morale.add(t.team_id)
            for ph in f.power_hit_ball_data:
                power[ph.hit_player] = (int(ph.formidable_attack), int(ph.formidable_defend))
            if n == len(order) - 1:
                last_ids.update(pd.player_id for pd in f.player_data)
        for t in morale:
            released[t] += 1
        if frames:
            g0 += frames[-1].frame_index
        acts.append((rally, winner, hand))
        wins.append(winner)
    conf = {i: p.info.conf_id for i, p in enumerate(room.home.formation.players)}
    conf.update({13 + i: p.info.conf_id for i, p in enumerate(room.away.formation.players)})
    for team, (side, lineup) in enumerate(((result.home, room.home), (result.away, room.away))):
        tactic = P.TeamTacticChosen()
        if side.bo_statistics:
            tactic.CopyFrom(side.bo_statistics[0].tactic_chosen)
        del side.bo_statistics[:]
        bo = side.bo_statistics.add()
        bo.tactic_chosen.CopyFrom(tactic)
        bo.score = sum(1 for w in wins if w == team)
        bo.round_num = len(order)
        side.morale_max, side.morale_num = 120, released[team]
        side.tactic_num = side.physical_recovery_total = 0
        stats = {}
        for i, p in enumerate(lineup.formation.players):
            if p.info.inst_id:
                pid = team * 13 + i
                st = bo.player_statistics.add(inst_id=p.info.inst_id, on_stage=pid in last_ids)
                a, d = power.get(pid, (0, 0))
                st.formidable_attack, st.formidable_defend, st.formidable_count = a, d, a + d
                stats[pid] = st
        for rally, winner, hand in acts:
            mine = [(g, a) for g, a in rally if (a.hit_player >= 13) == (team == 1)]
            rival = [(g, a) for g, a in rally if (a.hit_player >= 13) != (team == 1)]
            scorer = mine[-1][0] if winner == team and mine else None
            for g, a in mine:
                st = stats.get(a.hit_player)
                if st is None:
                    continue
                formula = 2 if team == 0 and a.formula_type in RECEIVE_FORMULAS else a.formula_type
                st.formula_statistics.add(formula=formula, result=a.hit_result, link=a.link, skill_id=a.skill_id,
                                          skill_win=g == scorer, frame_idx=g, crit=a.crit)
                if g == scorer:
                    st.score_skills.append(a.skill_id)
                if hand and a.formula_type == 1:  # the serve of a rally the user hand-served
                    st.serve_balls.append(hand)
            if winner == team:
                own = mine[-1][1] if mine else None
                riv = rival[-1][1] if rival else None
                bo.score_act_statistics.add(player_id=conf.get(own.hit_player, 0) if own else -1,
                                            touch_rival=conf.get(riv.hit_player, 0) if riv else 0,
                                            score_formula=own.formula_type if own else -1,
                                            rival_formula=riv.hit_player if riv else 0)
        bo.win = bo.score > sum(1 for w in wins if w == 1 - team)


def build_result(room: P.PVPRoomInfo, won: bool, stars: int, rewards: list[tuple[int, int]],
                 played: dict | None = None) -> bytes:
    res = _captured("0x0304", P.PVPResultS2C)
    br = res.battle_result
    br.info.CopyFrom(room)
    br.result.winner = HOME_WIN if won else 2
    br.star = stars if won else 0
    del br.rewards[:]
    for conf, n in rewards:
        br.rewards.add(conf_id=conf, number=n)
    del br.rewards_decomp[:]
    del br.reward_list[:]
    br.ClearField("assist_position")
    home = [p.info.inst_id for p in room.home.formation.players[:7] if p.info.inst_id]
    away = [p.info.inst_id for p in room.away.formation.players[:7] if p.info.inst_id]
    br.player_grade.Clear()
    # grades (shown as "Power" on the result screen): card power with some spread, best = MVP
    for side, team in ((br.player_grade.home, room.home), (br.player_grade.away, room.away)):
        graded = sorted(((p.power * random.randint(60, 120), p.info.inst_id)
                         for p in team.formation.players[:7] if p.info.inst_id), reverse=True)
        for grade, inst in graded:
            side.grades.add(inst_id=inst, grade=float(grade))
        if graded:
            side.max_grade_inst_id = graded[0][1]
    for side, ids in ((br.result.home, home), (br.result.away, away)):
        del side.use_sub_player_inst_id[:]
        for bo in side.bo_statistics:
            for st, inst in zip(bo.player_statistics, ids):
                st.inst_id = inst
            del bo.player_statistics[len(ids):]
    if played:  # a server-streamed match: statistics counted from its rallies, as Garena's
        live_statistics(br.result, room, played)
    # otherwise (scripted presets) the captured match statistics (score per set) stay
    br.result.home.bo_win_count, br.result.away.bo_win_count = (1, 0) if won else (0, 1)
    del br.result.play_back_records[:]
    return res.SerializeToString()
