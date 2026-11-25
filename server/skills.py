"""
Skill interpreter (match engine stage 3): runs the game's own skill tables during a rally.

Data (decoded/analysis/skill_tables.md):
  card skills  = PlayerCard.skills (skilllist ids, base + level-1) -> skilllist.Skillid -> skill rows
               + resonance skills (resonanceskilldetail <card>50<n>, TotalLevel <= resonance level, AddSkill)
  skill row    = TimePoint, Trigger[] (AND of {Key, Args, Cmp, Value}), CD (CDType unit x CDValue),
                 RemainEnergy / EnergyCostList (stamina), SkillEffect[] (AddBuff, AddBuffByLevel, RemoveBuff,
                 LetSkillCD, OverrideSkill ...), Before/AfterSkill chains
  buff row     = Type (2 attribute, 6 action power, 1 marker, 19 crit, 3 result override, 21 morale,
                 5 CD change, 7 armed), ModifyTarget, ModifyType (1 flat, 2 percent), ArgsType/Args,
                 Time unit x TimeValue, OverlayType/OverlayLimit, MountType (1 player, 2 team)

What is approximated (no data says it exactly): the event a timing fires on, the meaning of a few
condition arguments, when a Type 7 (armed) buff converts (here: at the owner's next own touch),
and stacking details. Unknown condition keys fail, so a skill we cannot read does not fire.
Driving skills (ultimates, cast by the user) are not run here (engine stage 4).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent
CFG = ROOT / "gamedata" / "cfg"
TYPED = ROOT / "decoded" / "analysis" / "typed" / "skill.json"
NONE = -2147483648
HOST, GUEST = 0, 1
TEAM_IDS = 13

# skill Type bit flags
DRIVING, PASSIVE = 4, 8
# TimeDef units for buff durations and cooldowns
T_MATCH, T_SET, T_RALLY, T_OWN_SCORE, T_OTHER_SCORE, T_NET, T_TOUCH = 1, 2, 3, 4, 5, 6, 7
T_SERVE, T_DIG_SERVE, T_SAVE, T_SET_TOUCH, T_SPIKE, T_BLOCK, T_DIG_SPIKE, T_DIG_NO_ATTACK = range(8, 16)
T_BOTH_TOUCH, T_OWN_TOUCH, T_OTHER_SPIKE = 18, 19, 20
ENERGY_MAX = 120.0
BOND_NEEDS_MEMBERS = True
BUFF_CHANGE_EVENT = True
FRESH_AT_END = True
SNAPSHOT = True  # ArgsType 10 values are fixed when the buff is given
TEAM_BUFF_EVENT = True
EARLY_TP5 = True
CRIT_EVENT = True
RESULT_KEYS = {"TouchBallEvaluate", "IsFormulaCrit", "IsCurrentFormulaCrit", "OwnFRWithStep", "OtherFRWithStep",
               "OwnFormulaResult", "OwnTounchBallNumber", "LastFormula"}
TOUCH_OK = {1: 1, 2: 3, 3: 3, 4: 4, 5: 4, 6: 2, 7: 3, 8: 3}  # formula -> lowest result that is not BAD
TOUCH_TOP = {1: 1, 2: 5, 3: 5, 4: 6, 5: 6, 6: 3, 7: 5, 8: 5}  # formula -> the PERFECT result
COUNTERS = {0: 3, 3: 1, 1: 2, 2: 0}  # Lineup Specialty -> the one it counters
STATE_SKILLS = True
_CHECKABLE: dict[int, bool] = {}  # skill id -> all its trigger keys are known
USE_UP = False  # an armed buff stays when its lent skill has run (Garena never sent its removal)
OWN_CLOCKS = False  # OverlayType 1 stacks with their own duration: no gain on the captures so far
GRANTED = True  # armed buffs lend skills (False: the older "armed buff turns into its inner buff")
STAMINA = 8  # property id
FATIGUE_20, FATIGUE_50, FATIGUE_IMMUNE = 20019, 20020, 20021
FORMULA_UNIT = {1: T_SERVE, 2: T_DIG_SERVE, 3: T_SAVE, 4: T_SET_TOUCH, 5: T_SPIKE, 6: T_BLOCK, 7: T_DIG_SPIKE,
                8: T_DIG_NO_ATTACK}
CATCH = {2, 3, 7, 8}
FORMULA_PROP = {1: 5, 2: 4, 3: 6, 4: 2, 5: 1, 6: 3, 7: 4, 8: 4}  # stat each formula uses
TOUCH_KEYS = {"GetSelfFormulaType", "SelfFormulaIsCatch", "SelfTouchBallMan", "MyTouchBallSkillTagType",
              "TouchBallSkillTagType", "OwnPlayerUniqueSkillTagType", "TouchBallPlayerProfile",
              "SelfFormulaTypeIsAttack", "OwnTounchBallNumber", "TouchBallEvaluate",
              "TouchBallSkillEnergyCostList",
              "OwnPlayerTouchBallSkillTagType", "TouchBallTeam", "Link", "IsFormulaCrit", "IsCurrentFormulaCrit",
              "TouchBallSkillUnique", "LastFormula", "IAttackType", "OwnFRWithStep", "OtherFRWithStep",
              "OwnFormulaResult", "SelfIsLastTouchBallMan", "OwnLastTouchBallManIsSelf", "ParticipateMultipleBlock"}
MORALE_MAX = 120


def _load(name):
    return json.loads((CFG / f"{name}.json").read_text(encoding="utf-8"))


def _list(v):
    if v is None or v == NONE:
        return []
    return v if isinstance(v, list) else [v]


def _flat(v):
    """Some table columns are nested lists; flatten them to ints."""
    out = []
    for x in _list(v):
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def _num(v, default=0):
    return v if isinstance(v, int) and v != NONE else default


SKILLS = {s["ID"]: s for s in json.loads(TYPED.read_text(encoding="utf-8"))} if TYPED.exists() else {}
BUFFS = {b.get("ID", 0): b for b in _load("buff")} if (CFG / "buff.json").exists() else {}
SKILLLIST = {r["ID"]: r for r in _load("skilllist")} if (CFG / "skilllist.json").exists() else {}
RESONANCE = {r["ID"]: r for r in _load("resonanceskilldetail")} if (CFG / "resonanceskilldetail.json").exists() else {}
COACHBUFF = {r["ID"]: r for r in _load("coachbuff")} if (CFG / "coachbuff.json").exists() else {}
PLAYERS = {r["ID"]: r for r in _load("playerlist")} if (CFG / "playerlist.json").exists() else {}
TRAMMEL = {r["ID"]: r for r in _load("trammel")} if (CFG / "trammel.json").exists() else {}
AVAILABLE = bool(SKILLS and BUFFS)
# skills that only run as part of another one (Before/AfterSkill, OverrideSkill, LetSkillCD targets)
# or carry the touch itself (SkillBehaviour): never fired on their own
CHAINED = {x for s in SKILLS.values() for k in ("BeforeSkill", "AfterSkill") for x in _flat(s.get(k))} | {
    _num(e.get("V1")) for s in SKILLS.values() for e in s.get("SkillEffect") or []
    if e.get("Key") == "OverrideSkill"} | {
    s["ID"] for s in SKILLS.values() if any(e.get("Key") == "SkillBehaviour" for e in s.get("SkillEffect") or [])}


def card_skills(card, resonance: bool = True) -> list[tuple[int, int]]:
    """(skill row id, level) for a PlayerCard: its 4 skill slots plus unlocked resonance skills."""
    out = []
    for sl in card.skills:
        row = SKILLLIST.get(sl)
        if not row:
            continue
        level = _num(row.get("Skilllevel"), 1)
        out += [(sid, level) for sid in _list(row.get("Skillid"))]
    reso = card.skill_resonance_unlock_level if resonance else -1
    for tier in range(1, 6):
        row = RESONANCE.get(card.conf_id * 1000 + 500 + tier)
        if row and _num(row.get("TotalLevel"), 99) <= reso:
            out += [(sid, 1) for sid in _flat(row.get("AddSkill"))]
    return [(sid, lv) for sid, lv in out if sid in SKILLS]


# ---------------------------------------------------------------- state

@dataclass
class Buff:
    id: int
    row: dict
    stacks: int
    left: int | None  # remaining duration in row Time units (None = until removed)
    caster: int
    level: int = 1  # level of the skill that gave it (armed buffs convert to the inner buff of that level)
    lefts: list | None = None  # OverlayType 1: remaining duration of every stack (each has its own clock)
    snap: int | None = None  # ArgsType 10: stacks of the counted buff when this buff was given


@dataclass
class Ctx:
    """What is happening when skills are checked."""
    event: str  # "start", "touch", "after", "end", "over"
    actor: int | None = None
    formula: int = 0
    link: int = 0
    result: int = -1
    crit: bool = False
    skill_id: int = 0  # skill the toucher uses (default action or an override)
    scorer: int | None = None
    driving: bool = False  # checking a manually cast skill (ultimate / morale)
    gate: str = ""  # "spike", "block" or "catch": which ultimate trigger this touch is
    hand: int = 0  # hand serve result 1 bad / 2 normal / 3 good


@dataclass
class Cast:
    skill_id: int
    player: int
    level: int


@dataclass
class BuffChange:
    entity: tuple  # ("p", id) or ("t", team); ("e", id) = that player's stamina is now `count`
    buff_id: int
    count: int
    cd: int


class Battle:
    """Skill/buff state of one match."""

    def __init__(self, info, stats: dict):
        self.stats = stats  # base attributes (engine.stats_for)
        # Lineup Specialty (formation.genre: 0 Power ATK, 1 Quick ATK, 2 Block, 3 Receive, 4 none).
        # Rule text of the game: Power Attack counters Receive, Receive counters Quick Attack, Quick
        # Attack counters Block, Block counters Power Attack.
        genre = {HOST: info.home.formation.genre.value, GUEST: info.away.formation.genre.value}
        self.genre = genre
        self.counter = {t: COUNTERS.get(genre[t]) == genre[1 - t] for t in (HOST, GUEST)}
        self.cards = {}
        self.skills: dict[int, list[tuple[dict, int]]] = {}
        extras = {}  # pid -> skill ids Garena listed for the player (PlayerCardInfo.skill_extras)
        for team, t in ((HOST, info.home), (GUEST, info.away)):
            for i, p in enumerate(t.formation.players):
                if p.info.conf_id:
                    pid = team * TEAM_IDS + i
                    self.cards[pid] = p.info
                    extras[pid] = [e.skill_id for e in p.skill_extras if e.skill_id in SKILLS]
                    own = [(SKILLS[sid], lv) for sid, lv in card_skills(p.info, resonance=not extras[pid])]
                    # skill_extras = resonance, specialty sets, recalling and coach skills, as the
                    # server computed them for this room; used as is when present
                    self.skills[pid] = own + [(SKILLS[sid], self._extra_level(p, sid)) for sid in extras[pid]]
        self.buffs: dict[tuple, dict[int, Buff]] = {}  # entity -> buff id -> Buff
        self.jobs = {pid: _num(PLAYERS.get(c.conf_id, {}).get("Job")) for pid, c in self.cards.items()}
        for team, t in ((HOST, info.home), (GUEST, info.away)):
            lead = min((p for p in self.cards if self.team_of(p) == team), default=None)
            team_has_extras = any(extras.get(p) for p in self.cards if self.team_of(p) == team)
            if not team_has_extras:  # rooms we built ourselves: coach position buffs from the tables
                for jb in t.formation.coach_card.job_buffs:  # coachbuff row -> skill for that position
                    sid = _num(COACHBUFF.get(jb.buff_id, {}).get("CoachBuffId"))
                    for pid in [p for p in self.cards if self.team_of(p) == team and self.jobs.get(p) == jb.job]:
                        if sid in SKILLS:
                            self.skills[pid].append((SKILLS[sid], 1))
            if lead is not None and TEAM_BUFF_EVENT:  # the game's own marker rules (ids under 1000)
                self.skills[lead] += [(sk, 1) for sid, sk in SKILLS.items() if 0 < sid < 1000 and any(
                    t.get("Key") == "TeamBuffNumberChange" for t in sk.get("Trigger") or [])]
            if lead is not None:  # team-wide extras (e.g. 6100121 → ourTeam)
                self.skills[lead] += [(SKILLS[e.skill_id], 1) for e in t.skill_extras if e.skill_id in SKILLS]
            for fid in dict.fromkeys(t.formation.fetter_id_levels):  # bonds: the id is a skilllist id
                sl = SKILLLIST.get(fid)
                if sl and lead is not None:
                    # a bond's skills belong to its SkillCharacterID (trammel.json): triggers such as
                    # "I serve" are about that player, not about the first one of the line-up
                    tram = TRAMMEL.get(fid // 100, {})
                    # "Activate by deploying N players from this Bond": the room lists every bond the
                    # account owns, but only those with enough members in the line-up are active
                    # (captures: bond 1184 needs 5501, 5502 and 5503; with two of them its buff never came)
                    need = _num(tram.get("CharacterNumber"))
                    here = {self.cards[p].conf_id % 10000 for p in self.cards
                            if self.team_of(p) == team and p % TEAM_IDS < 7}
                    if BOND_NEEDS_MEMBERS and need and len(here & set(_flat(tram.get("CharacterID")))) < need:
                        continue
                    who = _num(tram.get("SkillCharacterID"))
                    owner = next((p for p in self.cards if self.team_of(p) == team
                                  and self.cards[p].conf_id % 10000 == who), None if who else lead)
                    if owner is None:
                        # the bond's skill character is not in this team: its skill does not run
                        # (captures: bond 1184 with only 5501 and 5502 never gave its round-start buff)
                        continue
                    self.skills[owner] += [(SKILLS[s], _num(sl.get("Skilllevel"), 1))
                                           for s in _list(sl.get("Skillid")) if s in SKILLS]
        self.cd: dict[tuple[int, int], list] = {}  # (pid, skill id) -> [unit, left]
        self.energy = {pid: ENERGY_MAX for pid in self.cards}
        self.morale = [0.0, 0.0]
        self.on_court: set[int] = set()
        self.was_on_court: set[int] = set()
        self.ever_on_court: set[int] = set()  # stood on the court in this set
        self.behind_libero: set[int] = set()  # replaced by their team's libero right now
        self.morale_active = [0, 0]  # touches left of a released morale skill
        self.front: dict[int, set] = {}  # team -> front-row player ids this rally
        self.last_winner: int | None = None
        # rotation slot (0..5) of the players on court: starters by line-up order, whoever comes in
        # takes the slot of the one who left. Diagonal partners are the slots three apart.
        self.slot = {pid: pid % TEAM_IDS for pid in self.cards if pid % TEAM_IDS < 6}
        self._copying = False
        self._lender: int | None = None  # caster of the armed buff whose lent skill is being checked
        self._cd_tag: int | None = None  # tag of the skill whose cooldown just ended (see _cd_done)
        self._tbuff: int | None = None  # team buff whose count just changed (see _team_buff_changed)
        self._fresh: set | None = None  # buffs given while the rally-end skills run
        self._pbuff: set = set()  # buffs whose change is being handled (see _buff_changed)
        self.history: list[tuple] = []  # (team, link, formula, result, player, crit) this rally
        self.casts: list[Cast] = []  # collected since the last flush
        self.changes: list[BuffChange] = []
        self.armed_fired: set = set()

    @staticmethod
    def _extra_level(entry, sid: int) -> int:
        """Level for an extra skill: recalling skills (2xxxxxxxx) use the recalling stage (buff
        215701111 + stage 5 - 1 = 215701115 as in the captures), the others level 1."""
        if 200000000 <= sid < 300000000 and entry.recalling_entity.stage:
            return entry.recalling_entity.stage
        return 1

    # ------------------------------------------------ helpers
    def flush(self):
        casts, changes = self.casts, self.changes
        self.casts, self.changes = [], []
        return casts, changes

    @staticmethod
    def team_of(pid: int) -> int:
        return HOST if pid < TEAM_IDS else GUEST

    def members(self, team: int) -> list[int]:
        return [p for p in self.cards if self.team_of(p) == team]

    def court(self, team: int) -> list[int]:
        return [p for p in self.on_court if self.team_of(p) == team]

    def stacks(self, entity: tuple, buff_id: int) -> int:
        b = self.buffs.get(entity, {}).get(buff_id)
        return b.stacks if b else 0

    # ------------------------------------------------ stamina
    def spend(self, pid: int, amount: float):
        """Stamina change of a player (negative amount = recovery), with the fatigue rule of the system
        skills 20019/20020: at 20 % or less all stats -20 % (buff 20019), at 5 % or less -50 % (20020)."""
        old = self.energy.get(pid, ENERGY_MAX)
        new = max(0.0, min(ENERGY_MAX, old - amount))
        if new == old:
            return
        self.energy[pid] = new
        self.changes.append(BuffChange(("e", pid), 0, new, 0))
        ratio = new / ENERGY_MAX
        me = ("p", pid)
        # system skills 20023-20025: on every stamina change the fatigue buff that no longer fits goes
        if self.stacks(me, FATIGUE_20) and (ratio <= 0.05 or ratio > 0.20):
            self.remove_buff(me, FATIGUE_20)
        if self.stacks(me, FATIGUE_50) and ratio > 0.05:
            self.remove_buff(me, FATIGUE_50)
        if not self.stacks(me, FATIGUE_IMMUNE):
            if 0.05 < ratio <= 0.20 and not self.stacks(me, FATIGUE_20):
                self.add_buff(me, FATIGUE_20, 1, pid)
            if ratio <= 0.05 and not self.stacks(me, FATIGUE_50):
                self.add_buff(me, FATIGUE_50, 1, pid)

    def _recover(self):
        """Rally end: the fatigue buffs that no longer fit are taken off (system skills 20016-20018).
        Stamina itself only comes back through skills (stamina buffs, see add_buff): in the captures
        the user's players got about 8 back per rally from their skills, the stage team nothing."""
        for pid in sorted(self.cards):
            ratio = self.energy.get(pid, ENERGY_MAX) / ENERGY_MAX
            me = ("p", pid)
            if self.stacks(me, FATIGUE_20) and (ratio <= 0.05 or ratio > 0.20):
                self.remove_buff(me, FATIGUE_20)
            if self.stacks(me, FATIGUE_50) and ratio > 0.05:
                self.remove_buff(me, FATIGUE_50)

    # ------------------------------------------------ buffs
    def add_buff(self, entity: tuple, buff_id: int, stacks: int, caster: int, level: int = 1):
        row = BUFFS.get(buff_id)
        if not row:
            return
        if (_num(row.get("Type")) == 2 and _flat(row.get("ModifyTarget")) == [STAMINA]
                and not _num(row.get("Time"), 0) and _num(row.get("ArgsType"), 1) == 1):
            # stamina buffs act once and stay (no duration, no RevertAddition): +/- Args / 10000
            pids = [entity[1]] if entity[0] == "p" else self.court(entity[1])
            for pid in pids:
                self.spend(pid, -self._args(row)[0] / 10000 * stacks)
            return
        if _num(row.get("MountType"), 1) == 2 and entity[0] == "p":
            entity = ("t", self.team_of(entity[1]))
        have = self.buffs.setdefault(entity, {})
        limit = _num(row.get("OverlayLimit"), 99) or 99
        left = _num(row.get("TimeValue"), 0) if _num(row.get("Time"), 0) else None
        for keeper in have.values():  # Type 13 with ModifyTarget [0, buff]: that buff lasts Args units
            if _num(keeper.row.get("Type")) == 13 and _flat(keeper.row.get("ModifyTarget")) == [0, buff_id]:
                left = max(left or 0, self._args(keeper.row)[0] // 10000)
        b = have.get(buff_id)
        own_clock = OWN_CLOCKS and left is not None and _num(row.get("OverlayType"), 2) == 1
        if b and _num(row.get("OverlayType"), 2) in (1, 3):
            b.stacks = min(limit, b.stacks + stacks)
            b.left = left
            if own_clock:  # every stack runs for itself; at the limit the oldest stacks are renewed
                b.lefts = sorted((b.lefts or []) + [left] * stacks)[-limit:]
                b.stacks = len(b.lefts)
        elif b:
            b.stacks = min(limit, max(b.stacks, stacks))
            b.left = left
        else:
            b = have[buff_id] = Buff(buff_id, row, min(limit, stacks), left, caster, level)
            if own_clock:
                b.lefts = [left] * b.stacks
        if _num(row.get("ArgsType")) == 10:
            a = self._args(row)
            who = entity[1] if entity[0] == "p" else None
            team_ = self.team_of(who) if who is not None else entity[1]
            b.snap = (self.stacks(("p", who), a[0]) if who is not None else 0) or self.stacks(("t", team_), a[0])
        self.changes.append(BuffChange(entity, buff_id, b.stacks, b.left or 0))
        if self._fresh is not None:
            self._fresh.add((entity, buff_id))
        self._buff_changed(entity, buff_id)
        if entity[0] == "t":
            self._team_buff_changed(entity[1], buff_id)
        btype = _num(row.get("Type"))
        if btype == 13:  # duration keeper (captures: 111851121 keeps the marker 55011131 for 99 rallies)
            tg = _flat(row.get("ModifyTarget"))
            kept = have.get(tg[1]) if len(tg) == 2 and tg[0] == 0 else None
            if kept is not None and kept.left is not None:
                kept.left = max(kept.left, self._args(row)[0] // 10000)
                self.changes.append(BuffChange(entity, kept.id, kept.stacks, kept.left))
        if btype == 21:  # morale
            team = entity[1] if entity[0] == "t" else self.team_of(entity[1])
            self.morale[team] = min(MORALE_MAX, max(0.0, self.morale[team] + self._args(row)[0] / 10000 * stacks))
        elif btype == 5:  # cooldown change
            self._cd_change(entity, row)

    def _buff_changed(self, entity: tuple, buff_id: int):
        """Skills with a BuffNumberChange trigger for that buff run for its holder (a team buff: for
        everyone on the team)."""
        if not BUFF_CHANGE_EVENT or buff_id in self._pbuff:
            return
        self._pbuff.add(buff_id)
        try:
            ctx = Ctx("pbuff")
            pids = [entity[1]] if entity[0] == "p" else [p for p in self.cards if self.team_of(p) == entity[1]]
            for pid in pids:
                for skill, lv in list(self.skills.get(pid, [])):
                    if any(t.get("Key") == "BuffNumberChange" and _num(t.get("Args")) == buff_id
                           for t in skill.get("Trigger") or []):
                        self._run(skill, pid, lv, ctx)
        finally:
            self._pbuff.discard(buff_id)

    def _team_buff_changed(self, team: int, buff_id: int):
        """Skills with a TeamBuffNumberChange trigger for that buff run (e.g. system skill 803: every
        change of Attack Tempo gives the team's Awareness buff 122 anew)."""
        if self._tbuff is not None or not TEAM_BUFF_EVENT:
            return
        self._tbuff = buff_id
        try:
            ctx = Ctx("tbuff")
            for pid in [p for p in self.cards if self.team_of(p) == team]:
                for skill, lv in list(self.skills.get(pid, [])):
                    if any(t.get("Key") == "TeamBuffNumberChange" and _num(t.get("Args")) == buff_id
                           for t in skill.get("Trigger") or []):
                        self._run(skill, pid, lv, ctx)
        finally:
            self._tbuff = None

    def remove_buff(self, entity: tuple, buff_id: int, stacks: int = 99):
        have = self.buffs.get(entity, {})
        b = have.get(buff_id)
        if not b and entity[0] == "p":
            entity = ("t", self.team_of(entity[1]))
            have = self.buffs.get(entity, {})
            b = have.get(buff_id)
        if not b:
            return
        b.stacks -= stacks
        if b.stacks <= 0:
            del have[buff_id]
            self.changes.append(BuffChange(entity, buff_id, 0, 0))
        else:
            self.changes.append(BuffChange(entity, buff_id, b.stacks, b.left or 0))
        if entity[0] == "t":
            self._team_buff_changed(entity[1], buff_id)

    def tick(self, unit: int, team: int | None = None, pids=None, spare=None):
        """One unit of time passes: buffs and cooldowns counted in it run down. spare = (entity, buff)
        pairs that were given just now and keep their full time."""
        for entity, have in self.buffs.items():
            if pids is not None and (entity[0] != "p" or entity[1] not in pids):
                continue
            if team is not None and (entity[1] if entity[0] == "t" else self.team_of(entity[1])) != team:
                continue
            for bid in list(have):
                b = have[bid]
                if b.left is None or _num(b.row.get("Time")) != unit:
                    continue
                if spare and (entity, bid) in spare:
                    continue
                if b.lefts:
                    b.lefts = [x - 1 for x in b.lefts if x > 1]
                    if b.lefts:
                        if len(b.lefts) != b.stacks:
                            b.stacks = len(b.lefts)
                            self.changes.append(BuffChange(entity, bid, b.stacks, max(b.lefts)))
                        b.left = max(b.lefts)
                        continue
                    b.left = 1
                b.left -= 1
                if b.left <= 0:
                    del have[bid]
                    self.changes.append(BuffChange(entity, bid, 0, 0))
        for (pid, sid), c in list(self.cd.items()):
            if c[0] != unit:
                continue
            if pids is not None and pid not in pids:
                continue
            if team is not None and self.team_of(pid) != team:
                continue
            if self.cd.get((pid, sid)) is not c:  # ended meanwhile by a skill that ran in this loop
                continue
            c[1] -= 1
            if c[1] <= 0:
                del self.cd[(pid, sid)]
                self._cd_done(pid, sid)

    def _cd_done(self, pid: int, sid: int):
        """A skill is ready again: skills with a TagTypeSkillCDComplete trigger (TimePoint 11) of that
        player run, e.g. 51045211 "+10 % stats for everyone on court when the Block Ultimate is ready"."""
        tag = _num(SKILLS.get(sid, {}).get("SkillTagType"))
        if not tag or self._cd_tag is not None:
            return
        self._cd_tag = tag
        try:
            ctx = Ctx("cd", pid)
            # for everyone on the team: in the captures 51045211 of card 5104 also comes when a
            # teammate's Block Ultimate is ready again
            for mate in [p for p in self.cards if self.team_of(p) == self.team_of(pid)]:
                for skill, lv in list(self.skills.get(mate, [])):
                    if any(t.get("Key") == "TagTypeSkillCDComplete" for t in skill.get("Trigger") or []):
                        self._run(skill, mate, lv, ctx)
        finally:
            self._cd_tag = None

    def _cd_skills(self, pid: int, row) -> list[int] | None:
        """Skill ids of pid a cooldown buff (Type 5) acts on. ModifyTarget = [mode, selector, ...]:
        selector 2 = the listed skills, 5 = ultimate-type skills with a listed tag, 6 = Block Ultimates
        (skill text of card 5702: "Block Ultimate cooldown duration decreases by 4" is [1, 6] -4),
        3 = every skill. None = a selector we cannot read."""
        tg = _flat(row.get("ModifyTarget"))
        sel, rest = (tg[1] if len(tg) > 1 else 2), tg[2:]
        own = [sk for sk, _ in self.skills.get(pid, [])]
        if sel == 2:
            return list(rest)
        if sel == 3:
            return [sk["ID"] for sk in own]
        if sel in (5, 6):
            tags = set(rest) if sel == 5 else {2}
            return [sk["ID"] for sk in own if _num(sk.get("Type")) & DRIVING and _num(sk.get("SkillTagType")) in tags]
        return None

    def _cd_length(self, pid: int, skill: dict) -> int:
        """Cooldown a skill starts with: its CDValue and the cooldown-length buffs (mode 1) on the player."""
        base = max(_flat(skill.get("CDValue")) or [1])
        change = 0
        for b in self._active(pid):
            # modes 1 and 2 change the length (captures: 5702's own [2, 2, 57024111] -2 counts with the
            # [1, 6] -4 he gives: 12 - 6 is under 8, the 12 - 4 of card 5302 is not)
            if _num(b.row.get("Type")) == 5 and (_flat(b.row.get("ModifyTarget")) or [0])[0] in (1, 2):
                ids = self._cd_skills(pid, b.row)
                if ids and skill["ID"] in ids:
                    change += self._args(b.row)[0] // 10000 * b.stacks
        return max(base + change, 0)

    def _cd_change(self, entity, row):
        amount = self._args(row)[0]
        pids = [entity[1]] if entity[0] == "p" else self.members(entity[1])
        for pid in pids:
            for sid in self._cd_skills(pid, row) or []:
                c = self.cd.get((pid, sid))
                if not c:
                    continue
                if amount <= -900000:
                    self.cd.pop((pid, sid), None)
                    self._cd_done(pid, sid)
                else:
                    c[1] += amount // 10000
                    if c[1] <= 0:
                        self.cd.pop((pid, sid), None)
                        self._cd_done(pid, sid)

    @staticmethod
    def _args(row) -> list[int]:
        return _flat(row.get("Args")) or [0]

    def _buff_value(self, b: Buff, owner: int) -> float:
        """Magnitude (x10000 units) of one stack of b, by ArgsType."""
        args = self._args(b.row)
        at = _num(b.row.get("ArgsType"), 1)
        if at == 3 and len(args) >= 2:  # ratio of the owner's own property
            return self.attr(owner, args[0], raw=True) * 10000 * args[1] / 10000
        if at == 9 and len(args) >= 2:  # per stack of a buff the owner has right now, up to args[2]
            # (57013152: Awareness and Reflex +20 % per [Well-Rounded], at most 300 %)
            n = self.stacks(("p", owner), args[0]) or self.stacks(("t", self.team_of(owner)), args[0])
            v = args[1] * n
            return min(v, args[2]) if len(args) > 2 and args[2] > 0 else v
        if at == 10 and len(args) >= 2:  # per stack of another buff, counted when this buff was given
            # (57023123: 5.5 % per [Role Swap], which the same skill consumes right after; 122 is given
            # again whenever Attack Tempo changes), up to args[2]
            n = b.snap if b.snap is not None and SNAPSHOT else (
                self.stacks(("p", owner), args[0]) or self.stacks(("t", self.team_of(owner)), args[0]))
            v = args[1] * n
            return min(v, args[2]) if len(args) > 2 and args[2] > 0 else v
        if at == 13 and len(args) >= 3:  # share of the caster's current property, capped
            # Garena uses the caster's buffed value (captures: Oikawa SF 55015426 gave teammates the
            # full +1.0 Strength cap); buffs that copy stats are left out of it to avoid loops
            if self._copying:
                return 0.0
            self._copying = True
            try:
                v = self.attr(b.caster, args[0]) * 10000 * args[2] / 10000
            finally:
                self._copying = False
            return min(v, args[3]) if len(args) > 3 and args[3] > 0 else v
        return args[0]

    def _active(self, pid: int):
        """Buffs that act on player pid (own and team buffs)."""
        yield from self.buffs.get(("p", pid), {}).values()
        yield from self.buffs.get(("t", self.team_of(pid)), {}).values()

    # ------------------------------------------------ numbers used by the engine
    def attr(self, pid: int, prop: int, raw: bool = False) -> float:
        """Current property: base stat with Type 2 buffs (flat + percent)."""
        base = self.stats.get(pid, {}).get(prop, 0.0)
        flat = pct = 0.0
        if not raw:
            for b in self._active(pid):
                if _num(b.row.get("Type")) != 2:
                    continue
                targets = _flat(b.row.get("ModifyTarget"))
                hit = prop in targets or (prop < 7 and 200 + prop in targets)
                if not hit:
                    continue
                v = self._buff_value(b, pid) * b.stacks
                if _num(b.row.get("ModifyType"), 1) == 2 or 200 + prop in targets:
                    pct += v / 10000
                else:
                    flat += v / 10000
        return max(0.0, base * (1 + pct) + flat)

    def power_mult(self, pid: int, prop: int) -> float:
        """Action power multiplier ((倍率) in power_des) from Type 6 buffs on that stat."""
        m = 1.0
        for b in self._active(pid):
            if _num(b.row.get("Type")) == 6 and prop in _flat(b.row.get("ModifyTarget")):
                m += self._buff_value(b, pid) * b.stacks / 10000
        return max(m, 0.1)

    def force_crit(self, pid: int, formula: int) -> bool:
        return any(_num(b.row.get("Type")) == 19 and formula in _flat(b.row.get("ModifyTarget"))
                   for b in self._active(pid))

    def override_result(self, pid: int, formula: int, result: int) -> int:
        for b in self._active(pid):
            if _num(b.row.get("Type")) == 3 and formula in _flat(b.row.get("ModifyTarget")):
                pairs = self._args(b.row)
                for i in range(0, len(pairs) - 1, 2):
                    if pairs[i] == result:
                        return pairs[i + 1]
        return result

    # ------------------------------------------------ conditions
    def _last(self, team: int, link: int | None = None, formula: int | None = None):
        for h in reversed(self.history):
            if h[0] == team and (link is None or h[1] == link) and (formula is None or h[2] == formula):
                return h
        return None

    def _value(self, key: str, arg: int, owner: int, ctx: Ctx):
        team = self.team_of(owner)
        me = ctx.actor == owner
        if key == "RoundStart":
            return ctx.event == "start"
        if key == "RoundEnd":
            return ctx.event == "end"
        if key == "Score":
            return ctx.event == "end" and ctx.scorer == team
        if key == "TeamBeat":
            # "when your Lineup Specialty counters the opponent's" (skill texts); in the captures bond
            # 1213 ran only in the one match where the home specialty countered
            return self.counter[team]
        if key == "ParticipateMultipleBlock":  # our blocks: the front row joins every block
            return (ctx.formula == 6 and ctx.actor is not None and self.team_of(ctx.actor) == team
                    and (owner == ctx.actor or owner in self.front.get(team, ())))
        if key == "TeamSpecific":  # Lineup Specialty of our team (arg 1: of the opponents)
            return self.genre[1 - team if arg == 1 else team]
        if key == "TeamSpecificGroup":
            # skill text of 5701's ultimate: "when your Lineup Specialty is Power ATK or Receive" is
            # group 1 (57015111), "Block or Quick ATK" group 0
            return {0: 1, 3: 1, 1: 0, 2: 0}.get(self.genre[team], -1)
        if key == "BallOverNet":
            return ctx.event == "over"
        if key == "ComeOnPlayer":
            # "player who came on": on the court now, or was on it earlier in the set and was subbed
            # out (not while sitting behind the libero). Matches the CC simulator rules found from
            # captures/videos (Aone AS R2/R4 and Kuroo School Flag R4 keep working after a sub-out).
            return owner in self.on_court or (owner in self.ever_on_court and owner not in self.behind_libero)
        if key == "ReplaceByL":
            return owner in self.behind_libero
        if key == "PlayerPosition":  # 7 = on court, 8 = on the bench (fits the captured RoundStart casts)
            return owner in self.on_court if arg == 7 else (owner not in self.on_court if arg == 8 else False)
        if key == "ToPosition":  # fires when the player comes onto the court (match start or sub-in)
            return ctx.event == "start" and owner in self.on_court and owner not in self.was_on_court
        if key == "GetSkillCD":
            c = self.cd.get((owner, arg))
            return c[1] if c else 0
        if key == "GetSelfFormulaType":
            # 0 for everyone who is not touching: "NE" without a value means "I am the one touching"
            # (it was -1, which made that true for all the others: Attack Tempo ran up six times as fast)
            return ctx.formula if me and ctx.event in ("touch", "after", "crit") else 0
        if key in ("SelfFormulaIsCatch",):
            return me and ctx.formula in CATCH
        if key in ("SelfTouchBallMan", "MyTouchBallSkillTagType", "OwnPlayerTouchBallSkillTagType"):
            if key == "OwnPlayerTouchBallSkillTagType":
                return ctx.actor is not None and self.team_of(ctx.actor) == team and ctx.event in ("touch", "after", "crit")
            return me and ctx.event in ("touch", "after", "crit") and (
                not arg or _num(SKILLS.get(ctx.skill_id, {}).get("SkillTagType")) == arg)
        if key == "TouchBallTeam":
            return ctx.actor is not None and self.team_of(ctx.actor) == team
        if key == "BuffNumber":
            return self.stacks(("p", owner), arg)
        if key == "TeamBuffNumber":
            return self.stacks(("t", team), arg) + sum(self.stacks(("p", p), arg) for p in self.members(team))
        if key == "OwnFRWithStep":
            h = self._last(team, link=arg)
            return h[3] if h else -1
        if key == "OtherFRWithStep":
            h = self._last(1 - team, link=arg)
            return h[3] if h else -1
        if key == "OwnFormulaResult":
            h = self._last(team, formula=arg)
            return h[3] if h else -1
        if key == "LastFormula":
            h = self.history[-2] if ctx.event == "after" and len(self.history) > 1 else (
                self.history[-1] if self.history else None)
            return h[2] if h else 0
        if key == "Link":
            return ctx.link == arg
        if key in ("IsFormulaCrit", "IsCurrentFormulaCrit") and ctx.event == "crit":
            return ctx.crit
        if key in ("IsFormulaCrit", "IsCurrentFormulaCrit"):
            return ctx.event == "after" and ctx.crit
        if key == "TouchBallSkillUnique":
            # the touch is played with a skill of the character, not with a plain action (captures of
            # 57018131: it comes with 57012121 serve, 57012211 receive and 57014111 ultimate, never with
            # 10020 / 10301 / 10601)
            return ctx.skill_id >= 100000
        if key == "GetProperty":
            return self.attr(owner, arg) * (10000 if arg >= 7 else 1)
        if key == "SkillResonanceNumber":
            return self.cards[owner].skill_resonance_unlock_level
        if key == "OwnPlayerPosition":
            return int(any(self.cards[p].conf_id == arg for p in self.court(team))) * 0xFFFF
        if key == "OwnLastTouchBallManIsSelf":
            h = self._last(team)
            return bool(h and h[4] == owner)
        if key == "SelfIsLastTouchBallMan":
            return bool(self.history and self.history[-1][4] == owner)
        if key == "IAttackType":
            if not me or ctx.formula != 5:
                return 0
            sub = SKILLS.get(ctx.skill_id, {}).get("SkillSubtype")
            # 1 = power attack, 2 = quick attack: in the captures 6100123 ("IAttackType 1", +20 % Power
            # Attack) only went to players spiking a NormalSpike, never to FastSpike ones
            if sub in ("FastSpike", "NormalSpike"):  # the attack he actually plays decides
                return 2 if sub == "FastSpike" else 1
            s = self.stats.get(owner, {})
            return 2 if s.get(0, 0) > s.get(1, 0) else 1
        if key == "Morale":
            return self.morale[team]
        if key == "IHaveDebuff":
            return any(_num(b.row.get("BuffType2")) == 1 for b in self._active(owner))
        if key == "DrivingCast":
            return ctx.driving
        if key == "SpikeTrigger":
            return ctx.driving and ctx.gate == "spike"
        if key == "BlockTrigger":
            return ctx.driving and ctx.gate == "block"
        if key == "CatchTrigger":
            return ctx.driving and ctx.gate == "catch"
        if key == "SelfProfile":
            return self.jobs.get(owner, 0)
        if key == "OwnNotInMoraleActive":
            return not self.morale_active[team]
        if key == "InMoraleActive":
            return bool(self.morale_active[team])
        if key == "ExceptCatchS":
            return True
        if key == "RecentTouchBallMan":
            # an attack ultimate is chosen while the set is being played: "recent" is then the touch
            # before the set (captures: 111851111 "not after 5501" is cast off 5501's own set)
            hist = self.history
            if ctx.driving and hist and hist[-1][2] == 4 and len(hist) > 1:
                hist = hist[:-1]
            return self.cards[hist[-1][4]].conf_id if hist else 0
        if key == "HandServeControlResult":
            return ctx.hand
        if key in ("HaveSkillTag", "HaveUniqueSkillTag"):
            return bool(self._tagged(owner, arg, key == "HaveUniqueSkillTag"))
        if key == "GetMyUniqueSkillTagCD":
            # cooldown length of my ultimate-type skill with that tag, with the cooldown buffs on me
            # (captures, card 5702: "-4" goes to teammates whose tag-2 skill has CD 12 or 10, the +15 %
            # stats to those who are then under 8)
            mine = [sk for sk, _ in self.skills.get(owner, [])
                    if _num(sk.get("SkillTagType")) == arg and _num(sk.get("Type")) & DRIVING]
            if not mine:
                return -1
            return max(self._cd_length(owner, sk) for sk in mine)
        if key in ("TouchBallSkillTagType", "OwnPlayerUniqueSkillTagType"):
            if ctx.event not in ("touch", "after", "crit") or ctx.actor is None:
                return False
            sk = SKILLS.get(ctx.skill_id, {})
            if key == "OwnPlayerUniqueSkillTagType" and (self.team_of(ctx.actor) != team
                                                         or _num(sk.get("SkillUniqueType")) != 1):
                return False
            tag = _num(sk.get("SkillTagType"))
            return tag == arg if arg else bool(tag)
        if key == "BattleEnergy":
            return self.energy.get(owner, ENERGY_MAX)
        if key == "BattleEnergyRatio":
            return self.energy.get(owner, ENERGY_MAX) / ENERGY_MAX * 10000
        if key == "TouchBallPlayerProfile":
            return self.jobs.get(ctx.actor, 0) if ctx.actor is not None else 0
        if key == "SelfFormulaTypeIsAttack":
            return me and ctx.formula in (1, 4, 5)
        if key == "TagTypeSkillCDComplete":  # see _cd_done
            return ctx.event == "cd" and self._cd_tag is not None and (not arg or arg == self._cd_tag)
        if key == "TouchBallSkillEnergyCostList":
            # stamina cost of the skill I touch with (x10000): "when he uses skills" asks for more than 1
            if not me or ctx.event not in ("touch", "after", "crit"):
                return 0
            return max(_flat(SKILLS.get(ctx.skill_id, {}).get("EnergyCostList")) or [0])
        if key == "BuffNumberChange":  # see _buff_changed: the count the buff has now
            if ctx.event != "pbuff" or arg not in self._pbuff:
                return 0
            return self.stacks(("p", owner), arg) or self.stacks(("t", team), arg)
        if key == "TeamBuffNumberChange":  # see _team_buff_changed
            return 0xFFFF if ctx.event == "tbuff" and arg == self._tbuff else 0
        if key == "HaveBuffTag":  # comes with a BuffNumber condition that names the buff
            return 0xFFFF
        if key == "OwnTounchBallNumber":
            # (sic) a touch of our team, attempts included (captures of 57023111 "when your players
            # touch the ball": serves, spikes and block jumps without a touch count too)
            return ctx.event in ("touch", "after", "crit") and ctx.actor is not None and self.team_of(ctx.actor) == team
        if key == "TouchBallEvaluate":
            # quality of a touch that passes the ball on to a teammate: 0 BAD (or not such a touch),
            # 1 normal, 2 PERFECT. Captures of 58011121 ("a touch that is not BAD"): receives and saves
            # from result 3, sets from 4, blocks only with a touch (2); never serves, spikes, stuffs
            if ctx.event != "after" or (arg and not me):
                return -1
            f, r = ctx.formula, ctx.result
            if f == 6:
                return 1 if r == 2 else 0
            if f in (1, 5) or r < TOUCH_OK.get(f, 3):
                return 0
            return 2 if r >= TOUCH_TOP.get(f, 5) else 1
        if key == "OtherLastTouchBallManAttackType":  # 1 power / 2 quick, like IAttackType
            h = self._last(1 - team)
            if not h:
                return 0
            st = self.stats.get(h[4], {})
            return 2 if st.get(0, 0) > st.get(1, 0) else 1
        if key == "UseSecondAttack":  # the setter's dump: not played by the engine
            return False
        if key == "OtherLastTouchBallIsCrit":
            h = self._last(1 - team)
            return bool(h and h[5])
        raise KeyError(key)

    def _tagged(self, owner: int, tag: int, unique: bool) -> list[int]:
        """Ids of the owner's skills with that SkillTagType (unique: only his ultimate-type ones; the
        captures show card 5701 counted with a tag-2 ultimate that is not marked SkillUniqueType)."""
        return [sk["ID"] for sk, _ in self.skills.get(owner, [])
                if _num(sk.get("SkillTagType")) == tag and (not unique or _num(sk.get("Type")) & DRIVING)]

    @staticmethod
    def _cmp(value, cmp: str, ref: int) -> bool:
        if cmp == "IF":
            return bool(value)
        if cmp == "NIF":
            return not value
        if isinstance(value, bool):
            value = int(value)
        if cmp == "E":
            return value == ref
        if cmp == "NE":
            return value != ref
        if cmp == "G":
            return value > ref
        if cmp == "GE":
            return value >= ref
        if cmp == "L":
            return value < ref
        if cmp == "LE":
            return value <= ref
        if cmp == "AND":
            return bool(int(value) & ref) if ref else bool(value)
        if cmp == "NAND":
            return not (int(value) & ref) if ref else not value
        return False

    def _passes(self, skill: dict, owner: int, ctx: Ctx) -> bool:
        for t in skill.get("Trigger") or []:
            try:
                v = self._value(t["Key"], _num(t.get("Args")), owner, ctx)
            except KeyError:
                return False
            if not self._cmp(v, t.get("Cmp", "IF"), _num(t.get("Value"))):
                return False
        return True

    # ------------------------------------------------ running skills
    def _targets(self, code: int, owner: int, ctx: Ctx, arg: int = 0) -> list[tuple]:
        """Buff targets by stateTargetDef (decoded/analysis/config_consts.txt); arg = the effect's V4
        (card id, buff id, position or attack type, depending on the target)."""
        team = self.team_of(owner)
        P_ = lambda ids: [("p", p) for p in ids]
        on = lambda t: self.court(t)
        bench = lambda t: [p for p in self.members(t) if p not in self.on_court]
        has = lambda p, bid: self.stacks(("p", p), bid) > 0
        if code in (0, 1, NONE):
            return [("p", owner)]
        if code == 4:
            return P_([ctx.actor] if ctx.actor is not None else [])
        if code in (5, 13):  # last toucher (13: the opponents' last one)
            want = team if code == 5 else 1 - team
            last = next((h[4] for h in reversed(self.history) if h[0] == want), None)
            return P_([last] if last is not None else [])
        if code in (2, 3, 16):  # players of a position (Job) arg
            t = 1 - team if code == 3 else team
            pool = on(t) if code != 16 else self.members(t)
            return P_([p for p in pool if self.jobs.get(p) == arg])
        if code in (6, 25, 17):  # lowest Block / highest Power Attack / highest total among our court
            pool = on(team)
            if not pool:
                return []
            if code == 6:
                return P_([min(pool, key=lambda p: self.attr(p, 3))])
            if code == 25:
                return P_([max(pool, key=lambda p: self.attr(p, 1))])
            return P_([max(pool, key=lambda p: sum(self.attr(p, k) for k in range(7)))])
        if code in (7, 8):  # least stamina (7 whole team, 8 on court)
            pool = self.members(team) if code == 7 else on(team)
            return P_([min(pool, key=lambda p: self.energy.get(p, 0))]) if pool else []
        if code in (100, 107):
            return P_(on(team))
        if code == 27:  # PlayerAtkCritRateLEX: our players on court whose Awareness (attack crit rate)
            # is at most arg / 10000
            return P_([p for p in on(team) if self.attr(p, 9) <= arg / 10000])
        if code == 21:  # DiagonalPlayer: the teammate three rotation slots from the owner
            mine = self.slot.get(owner)
            if mine is None:
                return []
            return P_([p for p in on(team) if self.slot.get(p) == (mine + 3) % 6])
        if code in (18, 19):  # (own / other) on-court player that is card arg, else everyone there
            t = team if code == 18 else 1 - team
            pool = on(t)
            return P_([p for p in pool if self.cards[p].conf_id == arg] if arg else pool)
        if code == 101:
            return P_(bench(team))
        if code == 201:
            return P_(bench(1 - team))
        if code == 102:
            return P_(self.members(team))
        if code == 108:
            return P_([p for p in on(team) if p != owner])
        if code == 109:  # own players of attack type arg (1 power / 2 quick, see IAttackType)
            typ = lambda p: 2 if self.stats.get(p, {}).get(0, 0) > self.stats.get(p, {}).get(1, 0) else 1
            return P_([p for p in on(team) if typ(p) == arg])
        if code in (22, 29):  # players of our / their team that carry buff arg
            t = team if code == 22 else 1 - team
            return P_([p for p in self.members(t) if has(p, arg)])
        if code in (14, 31):  # players on our court with a debuff (14: one of them)
            pool = [p for p in on(team) if any(_num(b.row.get("BuffType2")) == 1 for b in self._active(p))]
            return P_(pool[:1] if code == 14 else pool)
        if code == 28:  # own on-court players with a skill of tag arg
            return P_([p for p in on(team)
                       if any(_num(sk.get("SkillTagType")) == arg for sk, _ in self.skills.get(p, []))])
        if code == 106:
            return [("t", team)]
        if code == 200:
            return P_(on(1 - team))
        if code == 202:
            return P_(self.members(1 - team))
        if code == 206:
            return [("t", 1 - team)]
        if code in (104, 105, 204, 205):
            t = team if code in (104, 105) else 1 - team
            front = self.front.get(t)
            if not front:
                return P_(on(t))
            return P_([p for p in on(t) if (p in front) == (code in (104, 204))])
        return [("p", owner)]

    def _effects(self, skill: dict, owner: int, level: int, ctx: Ctx):
        for e in skill.get("SkillEffect") or []:
            k = e.get("Key")
            v1, v2, v3, v4 = _num(e.get("V1")), _num(e.get("V2")), _num(e.get("V3")), _num(e.get("V4"))
            if k == "AddBuff":
                for ent in self._targets(v3, owner, ctx, v4):
                    self.add_buff(ent, v1, max(v2, 1), owner, level)
            elif k == "AddBuffByLevel":
                for ent in self._targets(v2, owner, ctx, v3):
                    self.add_buff(ent, v1 + level - 1, 1, owner, level)
            elif k == "AddBuffBySelfBuffValue":
                # V2 with as many stacks as there are of V1 (58013171: the [Maintain] stacks consumed
                # at the end of the rally are kept as 58013151 for the next rally's bonus)
                n = self.stacks(("p", owner), v1) or self.stacks(("t", self.team_of(owner)), v1)
                if n > 0:
                    for ent in self._targets(v3, owner, ctx, v4):
                        self.add_buff(ent, v2, n, owner, level)
            elif k == "RemoveBuff":
                for ent in self._targets(v3, owner, ctx, v4):
                    self.remove_buff(ent, v1, v2 or 99)
            elif k == "RemoveBuffByLevel":
                for ent in self._targets(v3 or v2, owner, ctx):
                    self.remove_buff(ent, v1 + level - 1, 99)
            elif k == "LetSkillCD":
                row = SKILLS.get(v1)
                if row and _num(row.get("CDType")):
                    self.cd[(owner, v1)] = [row["CDType"], max(_flat(row.get("CDValue")) or [1])]
            elif k == "OverrideSkill":
                row = SKILLS.get(v1)
                if row and ctx.actor == owner:
                    ctx.skill_id = v1
                    self._run(row, owner, level, ctx, check=False)

    def _run(self, skill: dict, owner: int, level: int, ctx: Ctx, check: bool = True) -> bool:
        sid = skill["ID"]
        if check:
            if (owner, sid) in self.cd:
                return False
            need = _num(skill.get("RemainEnergy")) / 10000
            if need and self.energy.get(owner, 0) < need:
                return False
            if not self._passes(skill, owner, ctx):
                return False
        for before in _flat(skill.get("BeforeSkill")):
            if before in SKILLS:
                self._run(SKILLS[before], owner, level, ctx, check=False)
        self._effects(skill, owner, level, ctx)
        for after in _flat(skill.get("AfterSkill")):
            if after in SKILLS:
                self._run(SKILLS[after], owner, level, ctx, check=False)
        cost = max(_flat(skill.get("EnergyCostList")) or [0]) / 10000
        if cost:
            self.spend(owner, cost)
        if _num(skill.get("CDType")):
            length = self._cd_length(owner, skill)
            if length > 0:
                self.cd[(owner, sid)] = [skill["CDType"], length]
        self.casts.append(Cast(sid, owner, level))
        return True

    @staticmethod
    def event_of(skill: dict) -> str:
        """Which engine event a passive skill is checked on: its event-type trigger key decides,
        else touch-related keys with the TimePoint (before/after the touch), else rally start."""
        keys = {t.get("Key") for t in skill.get("Trigger") or []}
        if not keys:  # no trigger at all: checked before every touch (Garena casts e.g. 55015431 then)
            return Battle._touch_event(skill, keys)
        if keys & {"RoundStart", "ToPosition"}:
            return "start"
        if "TagTypeSkillCDComplete" in keys:
            return "cd"
        if "TeamBuffNumberChange" in keys:
            return "tbuff"
        if "BuffNumberChange" in keys:
            return "pbuff"
        if keys & {"RoundEnd", "Score"}:
            return "end"
        if "BallOverNet" in keys:
            return "over"
        if keys & TOUCH_KEYS:
            return Battle._touch_event(skill, keys)
        return "start"

    @staticmethod
    def _touch_event(skill: dict, keys: set) -> str:
        """Before or after the touch. TimePoint 5 skills come with the announcement of the touch in the
        captures (57011121: the toucher's own touch already has the bonus) unless they ask for the
        outcome of the touch."""
        tp = _num(skill.get("TimePoint"))
        if tp == 6 and "IsCurrentFormulaCrit" in keys and CRIT_EVENT:
            return "crit"  # "when performing Critical plays": the critical touch itself has the bonus
        if tp == 5 and EARLY_TP5:
            return "after" if keys & RESULT_KEYS else "touch"
        return "after" if tp in (4, 5, 6, 8, 9) else "touch"

    def fire(self, ctx: Ctx, owners=None):
        """Check every passive skill that belongs to this event, for all owners (default: everyone)."""
        rows = []
        for pid in (owners if owners is not None else self.cards):
            for skill, lv in self.skills.get(pid, []):
                stype = _num(skill.get("Type"))
                if stype & DRIVING or (not skill.get("Trigger") and skill["ID"] in CHAINED):
                    continue
                if self.event_of(skill) != ctx.event:
                    continue
                rows.append((_num(skill.get("TriggerOrder"), 0), pid, skill, lv, None))
            if GRANTED:
                for skill, lv, armed in self._granted(pid):
                    if self.event_of(skill) == ctx.event:
                        rows.append((_num(skill.get("TriggerOrder"), 0), pid, skill, lv, armed))
        rows.sort(key=lambda r: r[0])
        for _, pid, skill, lv, armed in rows:
            if armed and not self.stacks(armed[0], armed[1]):
                continue  # the armed buff is gone meanwhile
            self._lender = armed[2] if armed else None
            ran = self._run(skill, pid, lv, ctx)
            self._lender = None
            if ran and armed and USE_UP:
                self.remove_buff(armed[0], armed[1])

    def _granted(self, pid: int):
        """Type 7 (armed) buffs lend their holder a skill: Args[0] is a skill id (956 of the 960 armed
        buffs name a skill with a trigger). The skill is checked like the player's own ones; when it
        runs, the armed buff is used up. Yields (skill, level, (entity, armed buff id))."""
        for entity in (("p", pid), ("t", self.team_of(pid))):
            for b in list(self.buffs.get(entity, {}).values()):
                if _num(b.row.get("Type")) != 7:
                    continue
                args = self._args(b.row)
                skill = SKILLS.get(args[0]) if args else None
                if skill and not _num(skill.get("Type")) & DRIVING and self._checkable(skill):
                    yield skill, b.level, (entity, b.id, b.caster)

    def _checkable(self, skill: dict) -> bool:
        """Whether every trigger key of the skill is one _value knows."""
        sid = skill["ID"]
        if sid not in _CHECKABLE:
            ok = True
            probe = Ctx("touch")
            for t in skill.get("Trigger") or []:
                try:
                    self._value(t["Key"], _num(t.get("Args")), next(iter(self.cards)), probe)
                except KeyError:
                    ok = False
                    break
                except Exception:
                    pass
            _CHECKABLE[sid] = ok
        return _CHECKABLE[sid]

    def _legacy(self, b: Buff) -> bool:
        """Armed buffs whose lent skill has a trigger we cannot read: they turn into their inner buff
        at the next fitting touch, as before (better than never, by the captures)."""
        if not GRANTED:
            return True
        args = self._args(b.row)
        skill = SKILLS.get(args[0]) if args else None
        if not skill:
            return True
        if not _num(skill.get("Type")) & DRIVING and self._checkable(skill):
            return False
        # only when the lent skill gives the buff that carries its own id: otherwise the id is another
        # buff by coincidence (235202212..15 are level variants of one +15 % buff and four skills)
        return any(isinstance(e, dict) and str(e.get("Key", "")).startswith("AddBuff") and e.get("V1") == args[0]
                   for e in _list(skill.get("SkillEffect")))

    def _granted_state(self, ctx: Ctx):
        """Lent skills that only ask for a state (no event key) are checked before every touch: an
        armed buff put on at rally start is used at the serve announcement, as in the captures."""
        for pid in list(self.cards):
            for skill, lv, armed in list(self._granted(pid)):
                if self.event_of(skill) != "start":
                    continue
                if any(t.get("Key") in ("RoundStart", "ToPosition") for t in skill.get("Trigger") or []):
                    continue
                if not self.stacks(armed[0], armed[1]):
                    continue
                self._lender = armed[2]
                ran = self._run(skill, pid, lv, ctx)
                self._lender = None
                if ran and USE_UP:
                    self.remove_buff(armed[0], armed[1])
            if not STATE_SKILLS:
                continue
            # own skills that only ask for a state and have a cooldown: also checked before a touch
            # (Garena: 55011141 comes with the serve announcement, once its marker buff is there)
            for skill, lv in self.skills.get(pid, []):
                keys = {t.get("Key") for t in skill.get("Trigger") or []}
                if (not keys or _num(skill.get("Type")) & DRIVING or not _num(skill.get("CDType"))
                        or self.event_of(skill) != "start" or keys & {"RoundStart", "ToPosition"}):
                    continue
                if keys == {"BuffNumber"}:
                    # asks only for a marker buff: it works for whoever on the team carries the marker
                    # (captures: 55011141 of card 5501 runs on his diagonal partner, who has 55011131)
                    for mate in self.court(self.team_of(pid)):
                        self._run(skill, mate, lv, ctx)
                else:
                    self._run(skill, pid, lv, ctx)

    def _arm(self, pid: int, formula: int = 0, prop: int = -1):
        """Type 7 (armed) buffs turn into their inner buff when the owner makes a touch the inner
        buff acts on: Type 6 power on the stat this touch uses, Type 19 crit / Type 3 result
        override on this formula. Other armed buffs have triggers the tables don't spell out and
        stay armed (calibrate.py: converting them on any touch overshot Garena's values)."""
        for b in list(self.buffs.get(("p", pid), {}).values()):
            if _num(b.row.get("Type")) != 7 or not self._legacy(b):
                continue
            args = self._args(b.row)
            inner = BUFFS.get(args[0]) if args else None
            if not inner:
                continue
            itype, targets = _num(inner.get("Type")), _flat(inner.get("ModifyTarget"))
            fits = (itype == 6 and prop in targets) or (itype in (19, 3) and formula in targets)
            if not fits:
                continue
            self.remove_buff(("p", pid), b.id)
            self.add_buff(("p", pid), self._inner_id(args[0], b.level), max(args[1] if len(args) > 1 else 1, 1),
                          b.caster, b.level)

    @staticmethod
    def _inner_id(base: int, level: int) -> int:
        """Armed buffs hold the level-1 inner buff id; Garena applies the one of the caster's skill
        level (captures: armed 57011121 at level 3 -> 57011123, recalling stage 5 -> ...145)."""
        lv = base + level - 1
        return lv if level > 1 and lv in BUFFS and _num(BUFFS[lv].get("Type")) == _num(BUFFS[base].get("Type")) else base

    def _arm_all(self):
        """Armed buffs whose inner buff is not an action buff (stats, markers, CDs ...) turn into it
        at the next touch of anyone: in the captures they convert on the serve announcement right
        after RoundStart (e.g. 57021121 -> 57021141 for every player that has it)."""
        for entity, have in list(self.buffs.items()):
            for b in list(have.values()):
                if _num(b.row.get("Type")) != 7 or not self._legacy(b):
                    continue
                args = self._args(b.row)
                inner = BUFFS.get(args[0]) if args else None
                if not inner or _num(inner.get("Type")) in (6, 19, 3):
                    continue
                self.remove_buff(entity, b.id)
                self.add_buff(entity, self._inner_id(args[0], b.level), max(args[1] if len(args) > 1 else 1, 1),
                              b.caster, b.level)

    # ------------------------------------------------ events from the engine
    def rally_start(self, on_court: set[int], behind_libero: set[int] = frozenset(), front=None,
                    stance=None, subs=()):
        """stance: team -> the six players in rotation order (the engine knows it); subs: (on, off)
        pairs of this rally's substitutions (replays of captures). Both only keep the slots right."""
        self.front = {t: set(v) for t, v in (front or {}).items()}
        self.was_on_court = self.on_court
        self.on_court = set(on_court)
        self.ever_on_court |= self.on_court
        self.behind_libero = set(behind_libero)
        self.history = []
        for on, off in subs:
            if off in self.slot and on not in self.slot:
                self.slot[on] = self.slot.pop(off)
        for team in (HOST, GUEST):  # substitutes and the libero take over the slot of who they replace
            if stance and stance.get(team):
                # rotation moves everyone alike, so the order itself gives the diagonal partners
                for p in [q for q in self.slot if self.team_of(q) == team]:
                    del self.slot[p]
                for i, p in enumerate(stance[team]):
                    self.slot[p] = i
                continue
            here = sorted(p for p in self.on_court if self.team_of(p) == team)
            gone = sorted(p for p in self.slot if self.team_of(p) == team and p not in self.on_court)
            new = [p for p in here if p not in self.slot]
            for p, old in zip(new, gone):
                self.slot[p] = self.slot.pop(old)
        self.fire(Ctx("start"))

    def before_touch(self, pid: int, formula: int, link: int, skill_id: int) -> Ctx:
        ctx = Ctx("touch", pid, formula, link, skill_id=skill_id)
        # every action costs each player on court 1 stamina and the one who plays it 1 more (Garena's
        # property changes at every announcement)
        for p in sorted(self.on_court):
            self.spend(p, 2.0 if p == pid else 1.0)
        prop = FORMULA_PROP.get(formula, -1)
        if formula == 5 and self.attr(pid, 0) > self.attr(pid, 1):
            prop = 0
        if GRANTED:
            self._granted_state(ctx)
        self._arm(pid, formula, prop)
        self._arm_all()
        self.fire(ctx)
        return ctx

    def crit_known(self, ctx: Ctx, crit: bool):
        """Called once the touch is known to be critical or not, before its power is worked out: skills
        that ask for the current touch being critical (TimePoint 6) run now, so that their buff counts
        for this touch (5701's ultimate: "an additional 50 % when performing Critical plays")."""
        if not CRIT_EVENT:
            return
        event, ctx.event, ctx.crit = ctx.event, "crit", crit
        try:
            self.fire(ctx)
        finally:
            ctx.event = event

    def after_touch(self, ctx: Ctx, result: int, crit: bool):
        ctx.event, ctx.result, ctx.crit = "after", result, crit
        team = self.team_of(ctx.actor)
        self.history.append((team, ctx.link, ctx.formula, result, ctx.actor, crit))
        self.morale[team] = min(MORALE_MAX, self.morale[team] + 6)
        self.fire(ctx)
        if self.morale_active[team]:
            self.morale_active[team] -= 1
        self.tick(T_TOUCH, team=team)
        self.tick(T_BOTH_TOUCH)
        self.tick(T_OWN_TOUCH, team=team)
        unit = FORMULA_UNIT.get(ctx.formula)
        if unit:
            self.tick(unit, pids={ctx.actor})
        if ctx.formula == 5:
            self.tick(T_OTHER_SPIKE, team=1 - team)

    def ball_over(self):
        self.fire(Ctx("over"))
        self.tick(T_NET)

    def rally_end(self, winner: int):
        self.last_winner = winner
        self.morale[winner] = min(MORALE_MAX, self.morale[winner] + 10)
        self.morale[1 - winner] = min(MORALE_MAX, self.morale[1 - winner] + 30)
        # buffs given by the rally-end skills are for the next rally: they do not lose a rally right
        # away (57023123 "at the end of each turn ... increases your players' ATK and DEF Technique")
        self._fresh = set() if FRESH_AT_END else None
        self.fire(Ctx("end", scorer=winner))
        fresh, self._fresh = self._fresh, None
        self.tick(T_RALLY, spare=fresh)
        self.tick(T_OWN_SCORE, team=winner, spare=fresh)
        self.tick(T_OTHER_SCORE, team=1 - winner, spare=fresh)
        self._recover()

    # ------------------------------------------------ manual casts (ultimates, morale skill)
    GENERIC = {"spike": (10301, 10401), "block": (10501,), "catch": (10601,)}

    def options(self, team: int, gate: str, players) -> list[tuple[int, int]]:
        """(player, skill) ultimates offered in a window: driving skills whose trigger has this gate,
        not cooling down, with enough stamina; plus the generic action ultimate."""
        key = {"spike": "SpikeTrigger", "block": "BlockTrigger", "catch": "CatchTrigger"}[gate]
        out = []
        for pid in players:
            own = []
            for skill, lv in self.skills.get(pid, []):
                if not _num(skill.get("Type")) & DRIVING or _num(skill.get("Type")) & 16:
                    continue
                if key not in {t.get("Key") for t in skill.get("Trigger") or []}:
                    continue
                if (pid, skill["ID"]) in self.cd:
                    continue
                need = max(_num(skill.get("RemainEnergy")), max(_flat(skill.get("EnergyCostList")) or [0])) / 10000
                if self.energy.get(pid, 0) < need:
                    continue
                own.append(skill["ID"])
            for sid in self.GENERIC[gate]:
                row = SKILLS.get(sid)
                if row and self._profile_ok(row, pid):
                    own.append(sid)
                    break
            out += [(pid, sid) for sid in dict.fromkeys(own)]
        return out

    def _profile_ok(self, row, pid) -> bool:
        for t in row.get("Trigger") or []:
            if t.get("Key") == "SelfProfile" and not self._cmp(self.jobs.get(pid, 0), t.get("Cmp"), _num(t.get("Value"))):
                return False
        return True

    def cast_driving(self, ctx: Ctx, sid: int) -> bool:
        """The toucher casts the chosen ultimate (conditions checked with DrivingCast true)."""
        skill = SKILLS.get(sid)
        if not skill:
            return False
        level = next((lv for s, lv in self.skills.get(ctx.actor, []) if s["ID"] == sid), 1)
        ctx.driving = True
        try:
            ok = self._run(skill, ctx.actor, level, ctx)
        finally:
            ctx.driving = False
        if ok:
            ctx.skill_id = sid
        return ok

    def morale_ready(self, team: int) -> bool:
        return self.morale[team] >= MORALE_MAX and not self.morale_active[team]

    def cast_morale(self, team: int, skill_id: int = 300, force: bool = False) -> bool:
        """Morale skill (300 = all players +20 % basic stats for 8 touches; cards can swap in their own
        variant, e.g. 311). Morale goes back to 0."""
        if not force and not self.morale_ready(team):
            return False
        skill = SKILLS.get(skill_id) or SKILLS.get(300)
        if not skill:
            return False
        owner = min(self.members(team))
        self._effects(skill, owner, 1, Ctx("touch"))
        self.casts.append(Cast(skill["ID"], owner, 1))
        self.morale[team] = 0.0
        self.morale_active[team] = 8
        return True

    # ------------------------------------------------ re-simulation support
    def snapshot(self):
        return ({e: {k: Buff(b.id, b.row, b.stacks, b.left, b.caster, b.level, list(b.lefts) if b.lefts else None, b.snap) for k, b in have.items()}
                 for e, have in self.buffs.items()},
                {k: list(v) for k, v in self.cd.items()}, dict(self.energy), list(self.morale),
                set(self.on_court), set(self.was_on_court), list(self.morale_active),
                set(self.ever_on_court), set(self.behind_libero), self.last_winner, dict(self.slot))

    def restore(self, snap):
        buffs, cd, energy, morale, on, was, active, ever, behind, self.last_winner, slot = snap
        self.slot = dict(slot)
        self.ever_on_court, self.behind_libero = set(ever), set(behind)
        self.buffs = {e: {k: Buff(b.id, b.row, b.stacks, b.left, b.caster, b.level, list(b.lefts) if b.lefts else None, b.snap) for k, b in have.items()}
                      for e, have in buffs.items()}
        self.cd = {k: list(v) for k, v in cd.items()}
        self.energy, self.morale = dict(energy), list(morale)
        self.on_court, self.was_on_court, self.morale_active = set(on), set(was), list(active)
        self.history, self.casts, self.changes = [], [], []
