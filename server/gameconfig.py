"""
Game config tables (gamedata/cfg/*.json, made by tools_config_dump.py; the folder follows edition.py).

Optional: if the dump is missing, AVAILABLE is False and callers fall back to
simpler behaviour.
"""
import json
import random
from pathlib import Path

import edition

CFG = edition.CFG  # gamedata/cfg, or gamedata-jp/cfg when the server runs the JP edition
AVAILABLE = (CFG / "cardlotteryconfig.json").exists()

# Lowest star seen per rarity in a real account's card pack = star of a fresh card
START_STAR = {1: 2, 2: 2, 3: 2, 4: 2, 5: 5, 6: 6}
PROB_SCALE = 10000  # BaseProbability units (340 -> 3.4 %)


def _table(name):
    return {r["ID"]: r for r in json.loads((CFG / f"{name}.json").read_text(encoding="utf-8"))}


def _as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def _weighted(entries):
    """'id=weight' strings -> [(id, weight)]"""
    out = []
    for e in _as_list(entries):
        if isinstance(e, str) and "=" in e:
            a, b = e.split("=", 1)
            out.append((int(a), int(b)))
    return out


if AVAILABLE:
    LOTTERIES = _table("cardlotteryconfig")
    PLAYERS = _table("playerlist")
    SKILLS = _table("skilltemplate")
    MILESTONES = _table("lotterytimerewardconfig")
    STAGES = _table("stage")
    PHYSICAL = _table("playerphysical")
    ITEMS = _table("item")
    GIFTS = _table("gift")
    SIGN_DAILY = _table("signin")  # ID = month*100 + day
    SIGN_TOTAL = _table("totalsignin")
    TASKS = _table("taskdepotconfig")
    ACTIVITY_CHESTS = _table("dailyactivityreward")
    ACHIEVEMENT_REWARDS = _table("achievementpointreward")  # ID -> TaskTeamId, Point, Reward
    ASSISTANT = _table("gamehelperconf")  # ID = AssistantUnit.func_type -> Order, Type, Value1 (default arg)
    STAR_LIMITS = _table("upgradestar")  # ID = star -> Levellimit
    TRAIN = _table("train")  # ID = card_id * 10 + step
    SKILL_LIST = _table("skilllist")  # ID = card*1000 + slot*100 + level
    SOURCE_GROUPS = _table("sourcegather")  # Practice Match tickets: StageList per group
    CHAPTERS = _table("chapter")  # Stage = stage ids, Reward = 'stars:item:amount' chests
    PLAYER_LEVELS = _table("playerlevel")  # ID = manager level, LevelExp = EXP from ID-1, Reward on reaching
    LEVEL_EXP = {(r["Leveltype"], r["Level"]): r["Experience"]
                 for r in json.loads((CFG / "level.json").read_text(encoding="utf-8"))}
    POOLS = {}
    for row in json.loads((CFG / "lotterypoolconfig.json").read_text(encoding="utf-8")):
        # RolesID entries are 'weight=card_id'
        POOLS.setdefault(row["GropID"], []).extend(
            (card, weight) for weight, card in _weighted(row.get("RolesID")))


def parse_rewards(spec) -> list[tuple[int, int]]:
    """'item:amount', 'a:b,c:d' or a list of them -> [(item, amount)]"""
    out = []
    for e in _as_list(spec):
        for part in e.split(",") if isinstance(e, str) else []:
            if ":" in part:
                item, amount = part.split(":")[:2]
                out.append((int(item), int(amount)))
    return out


def open_rewards(rewards: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Replace auto-open gift boxes (item.Useway 4 -> gift table) by their contents."""
    out = []
    for item, amount in rewards:
        gift = GIFTS.get(ITEMS.get(item, {}).get("Itemresult")) if AVAILABLE else None
        if not gift or gift.get("Autoopen") != 1:
            out.append((item, amount))
            continue
        fixed = [e.split(":") for e in _as_list(gift.get("Fixedreward")) if isinstance(e, str)]
        for _ in range(amount):
            out += [(int(e[-2]), int(e[-1])) for e in fixed]  # capture: box 105000047 (gift 1071) gave 108 x60
            entries = [e.split(":") for e in _as_list(gift.get("Randomreward")) if isinstance(e, str)]
            if gift.get("Rewardtype") == 2 and entries:  # one random entry 'weight:item:amount'
                w, i, a = random.choices(entries, [int(e[0]) for e in entries])[0]
                out.append((int(i), int(a)))
            else:
                out += [(int(e[-2]), int(e[-1])) for e in entries]
    return out


def daily_sign_rewards(month: int, day: int) -> list[tuple[int, int]]:
    row = SIGN_DAILY.get(month * 100 + day) if AVAILABLE else None
    return open_rewards(parse_rewards(row.get("Wards"))) if row else []


def total_sign_reward(sign_id: int) -> tuple[int, list[tuple[int, int]]] | None:
    """(required sign-in count, rewards) for a cumulative sign-in reward."""
    row = SIGN_TOTAL.get(sign_id) if AVAILABLE else None
    return (row["Signnum"], open_rewards(parse_rewards(row.get("Wards")))) if row else None


def task_rewards(task_id: int) -> list[tuple[int, int]] | None:
    row = TASKS.get(task_id) if AVAILABLE else None
    return open_rewards(parse_rewards(row.get("TaskItemReward"))) if row else None


MAX_CARD_LEVEL = 80
CARD_EXP = 102  # "EXP" currency spent on card levels
MATCH_EXP = 113  # "Match EXP": goes straight onto each starter below its level cap (not a currency)
GOLD = 100


def level_cap(star: int) -> int:
    row = STAR_LIMITS.get(star) if AVAILABLE else None
    return row.get("Levellimit", MAX_CARD_LEVEL) if row else MAX_CARD_LEVEL


def level_up_cost(conf_id: int, level: int, steps: int) -> int | None:
    """EXP to go from `level` up `steps` levels (level table, by the card's Leveltype)."""
    level_type = PLAYERS.get(conf_id, {}).get("Leveltype") if AVAILABLE else None
    if level_type is None:
        return None
    total = 0
    for lv in range(level, level + steps):
        exp = LEVEL_EXP.get((level_type, lv))
        if exp is None:
            return None
        total += exp
    return total


def train_step(conf_id: int, step: int) -> tuple[list[tuple[int, int]], int] | None:
    """(items, gold) for training step `step` of a card, or None when it doesn't exist."""
    row = TRAIN.get(conf_id * 10 + step) if AVAILABLE else None
    return (parse_rewards(row.get("Trainitem")), row.get("Goldconsume") or 0) if row else None


def skill_gold(conf_id: int) -> int:
    return (PLAYERS.get(conf_id, {}).get("Skillgold") or 0) if AVAILABLE else 0


def roll_skill_upgrade(skills: list[int]) -> int | None:
    """Index of the skill that levels up (weighted by Skilllevelweight; maxed skills excluded)."""
    choices = []
    for i, s in enumerate(skills):
        w = SKILL_LIST.get(s, {}).get("Skilllevelweight") or 0
        if w > 0 and (s + 1) in SKILL_LIST:
            choices.append((i, w))
    if not choices:
        return None
    return random.choices([i for i, _ in choices], [w for _, w in choices])[0]


def stage_cost(stage_id: int) -> tuple[int, int] | None:
    """(currency/item id, amount) to enter a stage, from stage.Cost 'id:amount'."""
    spec = STAGES.get(stage_id, {}).get("Cost") if AVAILABLE else None
    if not isinstance(spec, str) or ":" not in spec:
        return None
    item, amount = spec.split(":", 1)
    return int(item), int(amount)


def stamina_regen(player_level: int) -> tuple[int, int] | None:
    """(seconds per point, natural regeneration cap) from playerphysical."""
    row = PHYSICAL.get(player_level) if AVAILABLE else None
    return (row["RecoveryTime"], row["RecoveryMax"]) if row else None


def chapter_chests(chapter: int) -> tuple[list[int], dict[int, list[tuple[int, int]]]]:
    """(stage ids, {stars needed: rewards}) of a Training Camp chapter."""
    row = CHAPTERS.get(chapter) if AVAILABLE else None
    if not row:
        return [], {}
    chests = {}
    for e in _as_list(row.get("Reward")):
        stars, item, amount = (int(x) for x in e.split(":")[:3])
        chests.setdefault(stars, []).append((item, amount))
    return _as_list(row.get("Stage")), chests


def source_group(stage_id: int) -> tuple[int, list[int]] | None:
    """(group id, ordered stage ids) of the Practice Match ticket containing a stage."""
    for gid, row in (SOURCE_GROUPS.items() if AVAILABLE else []):
        stages = _as_list(row.get("StageList"))
        if stage_id in stages:
            return gid, stages
    return None


MAX_PLAYER_LEVEL = 60
MANAGER_EXP = (137, 106)  # both are "manager EXP" (ICON_wupin_jinglijingyan)


def player_level_exp(level: int) -> int | None:
    """Manager EXP needed to go from level-1 to `level`."""
    row = PLAYER_LEVELS.get(level) if AVAILABLE else None
    return row.get("LevelExp") if row else None


def player_level_reward(level: int) -> list[tuple[int, int]]:
    row = PLAYER_LEVELS.get(level) if AVAILABLE else None
    return open_rewards(parse_rewards(row.get("Reward"))) if row else []


def lottery_cost(lottery_id: int) -> tuple[int, int] | None:
    """(item_id, amount per pull) from ExpendOnce 'item:amount'."""
    row = LOTTERIES.get(lottery_id) if AVAILABLE else None
    spec = row and row.get("ExpendOnce")
    if not isinstance(spec, str) or ":" not in spec:
        return None
    item, amount = spec.split(":", 1)
    return int(item), int(amount)


def _pick_pool(groups) -> int | None:
    groups = _weighted(groups)
    if not groups:
        return None
    pool_id = random.choices([g for g, _ in groups], [w for _, w in groups])[0]
    cards = POOLS.get(pool_id)
    if not cards:
        return None
    return random.choices([c for c, _ in cards], [w for _, w in cards])[0]


def roll(lottery_id: int, pity: int) -> tuple[int | None, bool]:
    """One pull. Returns (card conf_id or None, hit) where hit = UR/SP result (resets pity)."""
    row = LOTTERIES[lottery_id]
    ur = (_as_list(row.get("URbaseProbability")) or [0])[0]
    sp = (_as_list(row.get("BaseProbability")) or [0])[0]
    rule = row.get("CumulativeRule") or 0
    if rule > 0 and pity + 1 >= rule:
        return _pick_pool(row.get("CumulativePool") or row.get("SuccessPools")), True
    r = random.randrange(PROB_SCALE)
    if r < ur:
        return _pick_pool(row.get("URsuccessPools")), True
    if r < ur + sp:
        return _pick_pool(row.get("SuccessPools")), True
    return _pick_pool(row.get("FailurePools")), False


def new_card_fields(conf_id: int) -> tuple[int, list[int]] | None:
    """(start star, skill ids) for a freshly obtained card."""
    if not AVAILABLE or conf_id not in PLAYERS:
        return None
    s = SKILLS.get(conf_id, {})
    skills = [s[k] for k in ("Skill1", "Skill2", "Skill3", "Skill4") if isinstance(s.get(k), int) and s[k] > 0]
    return START_STAR.get(PLAYERS[conf_id].get("Rare"), 2), skills


def train_skills(conf_id: int) -> dict[int, int]:
    """Skills a card only gets through training: {skill id: training step} (skilllist.Unlockcondition
    "2,<step>"; "1,<star>" skills come with the card). Garena capture neu2811 (new account): Hinata 3101
    and the recruited 1140 start without their third skill (Unlockcondition 2,7), 3102 etc. with it."""
    if not AVAILABLE:
        return {}
    s = SKILLS.get(conf_id, {})
    out = {}
    for k in ("Skill1", "Skill2", "Skill3", "Skill4"):
        sid = s.get(k)
        cond = str(SKILL_LIST.get(sid, {}).get("Unlockcondition", "")).split(",") if isinstance(sid, int) else []
        if len(cond) == 2 and cond[0].strip() == "2" and cond[1].strip().isdigit():
            out[sid] = int(cond[1])
    return out


def milestones(lottery_id: int) -> dict[int, list[tuple[int, int]]]:
    """{pulls: [(item, amount)]} from entries 'pulls-item-amount[-...]'."""
    row = LOTTERIES.get(lottery_id) if AVAILABLE else None
    group = MILESTONES.get(row.get("LotteryTimeRewardID")) if row else None
    out = {}
    for e in _as_list(group.get("Cumulative") if group else None):
        parts = e.split("-")
        out.setdefault(int(parts[0]), []).append((int(parts[1]), int(parts[2])))
    return out


# --- skill resonance ----------------------------------------------------------------------------
# PlayerCard field 11 (SkillResonanceUnlockLevel, tags from Config.dll) = the highest unlocked tier
# threshold. A tier (resonanceskilldetail.TotalLevel: 4/6/8/10/12) can be unlocked once the card's 4
# skill levels add up to it (checked against all 1390 cards of the captured account).
RESONANCE_FIELD = 11


def resonance_tiers(conf_id: int) -> list[int]:
    """TotalLevel thresholds of the card's resonance tiers, ascending (empty: no resonance)."""
    if not AVAILABLE:
        return []
    return sorted({int(r["TotalLevel"]) for r in resonance_rows(conf_id) if r.get("TotalLevel")})


def resonance_rows(conf_id: int) -> list[dict]:
    """resonanceskilldetail rows of the card's tiers (ids differ per card: 110101 or 5202501, so they
    are taken from resonanceskillmodel[Resonanceplate].ResonanceEffect)."""
    if not AVAILABLE:
        return []
    plate = PLAYERS.get(conf_id, {}).get("Resonanceplate")
    detail = _cached("resonanceskilldetail")
    return [detail[e] for e in (_cached("resonanceskillmodel").get(plate) or {}).get("ResonanceEffect", [])
            if e in detail]


def resonance_bonus(conf_id: int, unlocked: int) -> tuple[dict, int]:
    """({stat type 0-6: percent x100}, extra power) of the unlocked tiers (TotalLevel <= unlocked).
    AddAttritube pairs: 8 = attribute 200+stat, 16 = value per 10000; ResonancePower 16 = power."""
    pct, power = {}, 0
    for row in resonance_rows(conf_id):
        if int(row.get("TotalLevel") or 99) > unlocked:
            continue
        flat = []
        for x in row.get("AddAttritube") or []:
            flat += x if isinstance(x, list) else [x]
        for i in range(0, len(flat) - 3, 4):
            if flat[i] == 8 and flat[i + 2] == 16 and 200 <= flat[i + 1] <= 206:
                pct[flat[i + 1] - 200] = pct.get(flat[i + 1] - 200, 0) + flat[i + 3]
        rp = row.get("ResonancePower") or []
        if len(rp) >= 4 and rp[2] == 16:
            power += int(rp[3])
    return pct, power


def skill_level_sum(skill_ids) -> int:
    """Sum of the levels of a card's skills (skilllist.Skilllevel)."""
    table = _cached("skilllist")
    total = 0
    for sid in skill_ids:
        try:
            total += int(table.get(sid, {}).get("Skilllevel") or 0)
        except (TypeError, ValueError):
            pass
    return total


_CACHE: dict = {}


def _cached(name: str) -> dict:
    if name not in _CACHE:
        try:
            _CACHE[name] = _table(name)
        except Exception:
            _CACHE[name] = {}
    return _CACHE[name]

