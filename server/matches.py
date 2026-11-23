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
_CFG = Path(__file__).parent.parent / "gamedata" / "cfg"


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
    stage id itself, Extreme Training (FIGHT_CONTEST) its floor number."""
    raw = args.pvp_args[1] if len(args.pvp_args) > 1 else 0
    if args.pvp_type == P.FIGHT_CONTEST and raw and raw < CONTEST_STAGE_BASE:
        return CONTEST_STAGE_BASE + raw
    return raw


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
    return sum(a.val for a in attrs if a.type in (s[0] for s in STATS)) // SCALE


def card_entry(card: P.PlayerCard, stage: dict | None = None) -> P.PlayerCardInfo:
    attrs = card_attrs(card.conf_id, card.level, card.star, card.train, stage)
    return P.PlayerCardInfo(info=card, attr=attrs, power=card_power(attrs))


# --- teams ----------------------------------------------------------------
def _lineup(acc, fight_type: int) -> list[int]:
    team = P.TeamInfoS2C()
    team.ParseFromString(acc.b.pb("0x0B01"))
    chosen = next((c.line_up_chosen for c in team.fight_line_up_chosens if c.fight_type == fight_type), None)
    forms = list(team.formations)
    form = next((f for f in forms if f.lineup == chosen), forms[0] if forms else None)
    return list(form.inst_ids) if form else []


def home_team(template: P.PVPTeamInfo, acc, gid: int, name: str, fight_type: int) -> P.PVPTeamInfo:
    team = P.PVPTeamInfo()
    team.CopyFrom(template)  # tactics, morale, icons: kept from the captured room
    team.gamer_id = gid
    team.name = name
    del team.formation.players[:]
    del team.skill_extras[:]
    team.formation.ClearField("coach_card")
    del team.formation.fetter_id_levels[:]
    captured = {p.info.inst_id: p for p in template.formation.players}
    for inst in _lineup(acc, fight_type)[:TEAM_SIZE]:
        raw = acc.get_card(inst)
        if not raw:
            continue
        card = P.PlayerCard()
        card.ParseFromString(raw)
        real = captured.get(inst)
        if real and _same_card(real.info, card):
            # unchanged since the capture: real stats incl. specialties, coach and bonds
            entry = P.PlayerCardInfo()
            entry.CopyFrom(real)
            team.formation.players.append(entry)
        else:
            team.formation.players.append(card_entry(card))
    team.power = sum(p.power for p in team.formation.players[:7])
    if any(p.info.inst_id in captured for p in team.formation.players):
        # the captured room's team-wide extras (coach, bonds) belong to these cards
        team.formation.coach_card.CopyFrom(template.formation.coach_card)
        team.formation.fetter_id_levels.extend(template.formation.fetter_id_levels)
        team.skill_extras.extend(template.skill_extras)
    return team


def _same_card(a: P.PlayerCard, b: P.PlayerCard) -> bool:
    return (a.conf_id, a.level, a.star, a.train, list(a.skills)) == (b.conf_id, b.level, b.star, b.train, list(b.skills))


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
    info.home.CopyFrom(home_team(info.home, acc, gid, name, args.pvp_type))
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


def build_result(room: P.PVPRoomInfo, won: bool, stars: int, rewards: list[tuple[int, int]]) -> bytes:
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
    # captured match statistics (score per set) stay; only the winner flag follows the outcome
    br.result.home.bo_win_count, br.result.away.bo_win_count = (1, 0) if won else (0, 1)
    del br.result.play_back_records[:]
    return res.SerializeToString()
