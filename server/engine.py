"""
Rally logic (match engine stage 2): which touch follows which, the power of each touch and its
result, decided by the players' attributes the way Garena's server did it as far as the data shows.

* Touch chain: gamedata/cfg/formulamapping.json (next formula from the last formula/result/link).
* Power: power_des = attribute * multiplier * crit damage * damage bonus * power * restraint * k
  (decoded/analysis/frame_format.md 2.2). Skills add multipliers/buffs later (stage 3); without
  them multiplier, power and restraint are 1. hit_ball_power = 2 * V / k.
* Result: standardresult.json gives per result value (base, max, min) weights. The touch's power
  against the opposing touch it answers moves good results toward max and bad results toward min
  (or the other way round when weaker). The strength of that shift is calibrated on the captured
  Garena touches (decoded/match_msgs.json, 285 answered touches): x = tanh(K * ln(P_own / P_opp)).
* Result 0 ends the rally and the point goes to the other team.
"""
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

CFG = Path(__file__).parent.parent / "gamedata" / "cfg"
NONE = -2147483648
SERVE, DIG_SERVE, SAVE, SET, SPIKE, BLOCK, DIG_SPIKE, DIG_NO_ATTACK = range(1, 9)
HOST, GUEST = 0, 1
TEAM_IDS = 13
# Captured touches alone fit best around 0.1, but their power ratios are dominated by skill/crit
# swings this stage does not model yet. 0.8 makes a team with twice the stats win ~64 % of points
# (~80 % of 5-point matches).
K_ADVANTAGE = 0.8
MAX_CROSS = 20
JOB_LIBERO = 4  # battleconstant MaxCrossNetNum
# receives / digs / saves weigh in as if ~4.5x stronger (ln 1.5): Garena's digs came up perfect far more
# often than the raw spike-vs-dig power suggests (best fit on the captured touches, scratch fit3.py)
CATCH_BIAS = 1.5
DIG_ROW = 4
REROLL_FAIL = {DIG_SERVE: 0.8, SAVE: 0.35}
# chance that a dig fails: spike result -> (dig weaker than the spike, dig stronger)
DIG_FAIL = {4: (0.20, 0.0), 5: (0.20, 0.0), 6: (0.45, 0.27)}
BLOCK_CHANCE = 1.0  # every captured dig after a spike came after a block attempt

# attribute (proto AttrType) used by each formula, and whether it uses the attack or defence crit stats
ATTR = {SERVE: 5, DIG_SERVE: 4, SAVE: 6, SET: 2, SPIKE: 1, BLOCK: 3, DIG_SPIKE: 4, DIG_NO_ATTACK: 4}
ATTACK = {SERVE, SET, SPIKE, SAVE}
K = {BLOCK: 2, DIG_SPIKE: 2}  # (k) in power_des, 1 otherwise
# a result at or above this counts as good for the toucher (resultjudgeconfig-like)
GOOD = {SERVE: 1, DIG_SERVE: 3, SAVE: 3, SET: 4, SPIKE: 4, BLOCK: 2, DIG_SPIKE: 3, DIG_NO_ATTACK: 3}
ATTR_FAST = 0
ATTR_CRIT_RATE, ATTR_CRIT_DMG, ATTR_BONUS = 9, 10, 11
ATTR_DEF_CRIT_RATE, ATTR_DEF_CRIT_DMG, ATTR_DEF_BONUS = 12, 13, 14


def _table(name):
    return json.loads((CFG / f"{name}.json").read_text(encoding="utf-8"))


MAPPING = _table("formulamapping")
LIMIT = {r["Formula"]: r["Limit"] / 100 for r in _table("formulafactor") if r.get("Level1") == 71}
STANDARD = _table("standardresult")


def next_formula(formula: int, result: int, link: int) -> int | None:
    for r in MAPPING:
        if r["LastFormulaType"] == formula and r["LastResult"] == result and r["Link"] == link:
            return r["FormulaType"]
    return None


def weights(formula: int, last_result: int, link: int) -> list[tuple[int, int, int]]:
    for r in STANDARD:
        if r["Formula"] != formula:
            continue
        if r["LastResult"] != NONE and r["LastResult"] != last_result:
            continue
        if formula == SAVE and r["Link"] != link:
            continue
        return [tuple(map(int, g.split(","))) for g in r["Weights"].split("|")]
    return []


def roll_result(formula: int, last_result: int, link: int, x: float, rng=random) -> int:
    """Weighted result roll; x in [-1, 1] = the toucher's advantage."""
    w = weights(formula, last_result, link)
    if not w:
        return GOOD.get(formula, 3)
    out = []
    for r, (base, hi, lo) in enumerate(w):
        good = r >= GOOD[formula]
        if (x > 0) == good:
            v = base + abs(x) * (hi - base)
        else:
            v = base - abs(x) * (base - lo)
        out.append(max(v, 0.0))
    if sum(out) <= 0:
        out = [max(b, 0) for b, _, _ in w]
    return rng.choices(range(len(out)), weights=out)[0]


def advantage(own: float, opp: float, formula: int = 0) -> float:
    """Shift of the result weights towards the toucher's good results (+) or bad ones (-), capped at
    the formula's formulafactor.Limit (10 serve, 43 receive, 40 block/dig/save: percentage points).
    The cap is what makes Garena's digs still come up perfect against far stronger spikes; it fits
    the captured touches better than an uncapped curve (calibration: log-likelihood -563 vs -608)."""
    if own <= 0 or opp <= 0:
        return 0.0
    cap = LIMIT.get(formula, 0.40)
    bias = CATCH_BIAS if formula in (DIG_SERVE, SAVE, DIG_SPIKE, DIG_NO_ATTACK) else 0.0
    return max(-cap, min(cap, K_ADVANTAGE * math.log(own / opp) + bias))


# ---------------------------------------------------------------- player stats and power

def stats_for(info) -> dict[int, dict[int, float]]:
    """player id -> {attr type: value} from the room (val + extrval, unscaled)."""
    out = {}
    for team, t in ((HOST, info.home), (GUEST, info.away)):
        for i, p in enumerate(t.formation.players):
            if not p.info.conf_id:
                continue
            out[team * TEAM_IDS + i] = {a.type: (a.val + a.extrval) / (a.scaled or 10000) for a in p.attr}
    return out


@dataclass
class Power:
    value: float  # V of power_des
    ball: float  # hit_ball_power
    crit: bool
    des: str


# per-action crit rate / crit damage / bonus property (105..125) by the stat the action uses
ACTION_CRIT = {0: 105, 1: 108, 2: 111, 3: 114, 4: 117, 5: 120, 6: 123}


def crit_stats(get, formula: int, prop: int) -> tuple[float, float, float]:
    """Crit rate, crit damage and damage bonus of a touch: the general attack (Awareness, Strength,
    Attack Technique) or defence (Reflex, Spirit, Defense Technique) stats plus the action's own
    105..125 properties."""
    if formula in ATTACK:
        rate, dmg, bonus = get(ATTR_CRIT_RATE), get(ATTR_CRIT_DMG), get(ATTR_BONUS)
    else:
        rate, dmg, bonus = get(ATTR_DEF_CRIT_RATE), get(ATTR_DEF_CRIT_DMG), get(ATTR_DEF_BONUS)
    base = ACTION_CRIT.get(prop)
    if base:
        rate, dmg, bonus = rate + get(base), dmg + get(base + 1), bonus + get(base + 2)
    return rate, dmg, bonus


def power(stats: dict, pid: int, formula: int, rng=random, multiplier: float = 1.0, battle=None) -> Power:
    """power_des: attribute * multiplier * crit damage * damage bonus * power * restraint * k.
    With a skills.Battle the attributes include buffs and Type 6 buffs add to the multiplier."""
    get = (lambda p: battle.attr(pid, p)) if battle else (lambda p: stats.get(pid, {}).get(p, 0.0))
    prop = ATTR[formula]
    attr = get(prop)
    if formula == SPIKE:  # quick attackers hit with Quick Attack, power hitters with Power Attack
        fast = get(ATTR_FAST)
        if fast > attr:
            attr, prop = fast, ATTR_FAST
    attr = attr or 1000.0
    if battle:
        multiplier *= battle.power_mult(pid, prop)
    rate, dmg, bonus = crit_stats(get, formula, prop)
    # crit stats are percentages stored x10000 (2500 = 25 %), already divided
    crit = rng.random() < min(rate, 0.8) or bool(battle and battle.force_crit(pid, formula))
    crit_dmg = 1.5 + dmg if crit else 1.0
    dmg_bonus = 1.0 + bonus
    k = K.get(formula, 1)
    v = attr * multiplier * crit_dmg * dmg_bonus * k
    des = (f"{v:.3f} = (属性值){attr:.4f} * (倍率){multiplier:.4g} * (爆伤){crit_dmg:.4g} * (增伤){dmg_bonus:.4g}"
           f" * (威力)1 * (克制系数)1 * (k){k}")
    return Power(v, 2 * v / k, crit, des)


# ---------------------------------------------------------------- rally simulation

@dataclass
class Step:
    formula: int
    player: int
    result: int
    link: int
    power: Power
    touch_ball: bool = True
    over: bool = False  # this touch sends the ball to the other side
    skill_id: int = 0  # 0 = the default action skill; else a skill override (skills.Battle)
    original: int = -1  # result before a buff overrode it
    casts: list = field(default_factory=list)  # skills.Cast fired around this touch
    buffs: list = field(default_factory=list)  # skills.BuffChange around this touch
    window: tuple | None = None  # (index, gate, [(player, skill)]) ultimate choices for the user team
    morale_ready: bool = False  # the user team's morale skill became available with this touch
    morale_cast: bool = False  # the user released the morale skill right before this touch
    morale_team: int | None = None  # team whose morale skill was released right before this touch


@dataclass
class Rally:
    steps: list = field(default_factory=list)
    winner: int = HOST
    start: tuple = ((), ())  # (casts, buff changes) at rally start
    end: tuple = ((), ())  # ... at rally end


def team_of(pid: int) -> int:
    return HOST if pid < TEAM_IDS else GUEST


DEFAULT_SKILL = {SERVE: 10020, DIG_SERVE: 10100, SAVE: 10800, SET: 10200, SPIKE: 10300, BLOCK: 10500,
                 DIG_SPIKE: 10600, DIG_NO_ATTACK: 10700}


GATE = {SPIKE: "spike", BLOCK: "block", DIG_SPIKE: "catch"}
HAND_SERVE = {1: 0.85, 2: 1.0, 3: 1.2}  # power factor by hand serve result (bad / normal / good)
AI_CAST_CHANCE = 0.5


def simulate(court, serve_team: int, stats: dict, rng=random, battle=None, inputs=None, user=HOST,
             behind_libero=(), auto=False) -> Rally:
    """Plays one rally. court = rallygen.Court (stance, jobs, positions); battle = skills.Battle
    (optional) runs the players' skills and buffs around every touch. inputs = the user team's
    choices for this rally: {"hand": 1..3, "ults": {window index: [(player, skill)]},
    "morale": touch index}; the other team casts its ultimates itself."""
    rally = Rally()
    steps = rally.steps
    inputs = inputs or {}
    chosen: dict[tuple, tuple] = {}  # (team, gate) -> (player, skill) for the coming touch
    windows = [0]
    if battle:
        battle.rally_start(set(court.stance[HOST]) | set(court.stance[GUEST]), set(behind_libero),
                           {t: court.front(t) for t in (HOST, GUEST)})
        rally.start = battle.flush()

    def add(formula, pid, link, opp: Power | None, last_result: int, forced: int | None = None,
            opp_ball: float | None = None) -> Step:
        team = team_of(pid)
        morale_cast = bool(battle and team == user and inputs.get("morale") is not None
                           and len(steps) >= inputs["morale"] and battle.cast_morale(user))
        ai_morale = bool(battle and team != user and battle.cast_morale(team))  # the AI releases it at once
        ready_before = bool(battle and battle.morale_ready(user))
        ctx = battle.before_touch(pid, formula, link, DEFAULT_SKILL[formula]) if battle else None
        pick_ = chosen.pop((team, GATE.get(formula)), None)
        if battle and pick_ and pick_[0] == pid:
            ctx.gate = GATE[formula]
            battle.cast_driving(ctx, pick_[1])
        mult = 1.0
        if formula == SERVE and team == user and inputs.get("hand"):
            mult = HAND_SERVE.get(inputs["hand"], 1.0)
            if ctx:
                ctx.hand = inputs["hand"]
        pw = power(stats, pid, formula, rng, multiplier=mult, battle=battle)
        ref = opp_ball if opp_ball is not None else (opp.ball if opp else None)
        x = advantage(pw.ball, ref, formula) if ref else 0.0
        result = forced if forced is not None else roll_result(formula, last_result, link, x, rng)
        if formula == DIG_SPIKE and forced is None and ref:
            # how often Garena's digs fail depends on the spike's result and on who is stronger (92
            # captured digs: 45 % after a perfect spike against a weaker dig, none after a result-3 one)
            fail = DIG_FAIL.get(last_spike_result, (0.0, 0.0))[0 if pw.ball < ref else 1]
            if rng.random() < fail:
                result = 0
            else:
                for _ in range(8):
                    if result:
                        break
                    result = roll_result(formula, last_result, link, x, rng)
                result = result or 3
        # Garena's receives of a serve almost never fail (0 of 43 captured) and its saves fail less
        # often than the table says (38 %): part of the failed rolls are rolled again
        if forced is None and result == 0 and rng.random() < REROLL_FAIL.get(formula, 0.0):
            result = roll_result(formula, last_result, link, x, rng)
        if formula == SPIKE:
            result = min(max(result, 3), 6)
        st = Step(formula, pid, result, link, pw)
        if battle:
            new = battle.override_result(pid, formula, result)
            if new != result and forced is None:
                st.original, st.result = result, new
            if ctx.skill_id != DEFAULT_SKILL[formula]:
                st.skill_id = ctx.skill_id
            battle.after_touch(ctx, st.result, pw.crit)
            st.casts, st.buffs = map(list, battle.flush())
            st.morale_cast = morale_cast
            st.morale_team = user if morale_cast else (team if ai_morale else None)
            st.morale_ready = not ready_before and battle.morale_ready(user)
        steps.append(st)
        return st

    def window(set_step: Step):
        """Ultimate windows when a team sets: its attackers may spike with an ultimate, the other
        side's front row may block and its back row dig with one."""
        if not battle:
            return
        chosen.clear()
        atk = team_of(set_step.player)
        dfn = 1 - atk
        offers = {atk: battle.options(atk, "spike", [p for p in court.stance[atk] if p != set_step.player]),
                  dfn: battle.options(dfn, "block", court.front(dfn))
                  + battle.options(dfn, "catch", [p for p in court.stance[dfn] if p not in court.front(dfn)])}
        for team, opts in offers.items():
            if team == user and not auto:  # auto play: the user's team casts by itself like the AI
                idx = windows[0]
                windows[0] += 1
                set_step.window = (idx, "spike" if team == atk else "defense", opts)
                picks = [o for o in inputs.get("ults", {}).get(idx, []) if o in opts]
            else:
                own = [o for o in opts if o[1] >= 100000] or opts
                picks = [rng.choice(own)] if own and rng.random() < AI_CAST_CHANCE else []
            for pid, sid in picks:
                gate = "spike" if team == atk else ("block" if pid in court.front(team) else "catch")
                chosen[(team, gate)] = (pid, sid)

    def over(st: Step):
        st.over = True
        if battle:
            battle.ball_over()
            casts, buffs = battle.flush()
            st.casts += casts
            st.buffs += buffs

    def in_air() -> tuple:
        """Players who cannot take the next ball: the last toucher, and after a block the spiker too."""
        out = [prev.player]
        if prev.formula == BLOCK:
            out += [s.player for s in rally.steps[-3:] if s.formula == SPIKE]
        return tuple(out)

    def finish(winner: int) -> Rally:
        rally.winner = winner
        if battle:
            battle.rally_end(winner)
            rally.end = battle.flush()
        return rally

    def pick(team, formula, exclude=()):
        ids = [p for p in court.stance[team] if p not in exclude]
        if formula in (SPIKE, SET, BLOCK):  # the libero only receives, digs and saves
            ids = [p for p in ids if court.jobs.get(p) != JOB_LIBERO] or ids
        setter = court.setter(team)
        if formula in (DIG_SERVE, DIG_SPIKE, DIG_NO_ATTACK, SAVE):
            pool = [p for p in ids if p != setter] or ids
            weights_ = [stats.get(p, {}).get(ATTR[formula], 1) + 1 for p in pool]
            return rng.choices(pool, weights=weights_)[0]
        if formula == SET:
            return setter if setter not in exclude else rng.choice(ids)
        if formula == SPIKE:
            # Garena lets the back row attack as well: 67 of 121 captured spikes came from players
            # outside the front row (check_match.py), so everyone but the setter and the libero may spike
            pool = [p for p in ids if p != setter] or ids
            weights_ = [stats.get(p, {}).get(ATTR[SPIKE], 1) + 1 for p in pool]
            return rng.choices(pool, weights=weights_)[0]
        if formula == BLOCK:
            return rng.choice(court.front(team))
        return rng.choice(ids)

    server = court.stance[serve_team][0]
    # the serve is weighed against the receiving team's best receive
    recv = max((power(stats, p, DIG_SERVE, rng, battle=battle).ball for p in court.stance[1 - serve_team]),
               default=0)
    serve = add(SERVE, server, 1, None, NONE, opp_ball=recv)
    if serve.result == 0:
        return finish(1 - serve_team)
    over(serve)

    team, formula, link = 1 - serve_team, DIG_SERVE, 2
    attack: Step = serve  # last touch that came over the net (what defenders answer)
    last_spike_result = NONE
    crosses = 1
    prev: Step = serve
    while True:
        want = chosen.get((team, GATE.get(formula)))
        if formula == BLOCK:
            pid = want[0] if want and want[0] in court.front(team) else pick(team, BLOCK)
            st = add(BLOCK, pid, 5, attack.power, last_spike_result)
        elif formula == DIG_SPIKE:
            opp = attack.power
            if prev.formula == BLOCK and prev.result == 2:  # the block softened the spike
                opp = Power(opp.value * 0.6, opp.ball * 0.6, opp.crit, opp.des)
            forced = 0 if crosses >= MAX_CROSS else None
            # Garena's digs follow standardresult row LastResult 4 whatever the spike's result was: that
            # row fits the captured digs far better than the spike result's own row (log-likelihood
            # -138 vs -245 over 92 digs; scratch fit5.py)
            last = DIG_ROW
            # after a block the blocker and the spiker are still in the air: someone else digs
            air = in_air()
            pid = want[0] if want and want[0] not in air else pick(team, DIG_SPIKE, exclude=air)
            st = add(DIG_SPIKE, pid, 2, opp, last, forced)
        elif formula == DIG_SERVE:
            st = add(DIG_SERVE, pick(team, DIG_SERVE), 2, attack.power, NONE)
        elif formula == SET:
            st = add(SET, pick(team, SET, exclude=(prev.player,)), 3, None, prev.result)
            window(st)
        elif formula == SPIKE:
            # the receiver is still getting up from his dig: a teammate attacks (unless the user chose him)
            busy = tuple(s.player for s in rally.steps[-2:] if team_of(s.player) == team)
            pid = want[0] if want and want[0] != prev.player else pick(team, SPIKE, exclude=busy)
            st = add(SPIKE, pid, 4, None, prev.result)
        elif formula == SAVE:
            opp = attack.power if prev.formula == BLOCK else None
            air = in_air()
            st = add(SAVE, pick(team, SAVE, exclude=air), link, opp, NONE)
        else:  # DIG_NO_ATTACK
            st = add(DIG_NO_ATTACK, pick(team, DIG_NO_ATTACK), 2, attack.power, NONE)

        if st.result == 0:
            if st.formula in (DIG_SERVE, DIG_SPIKE, SAVE):
                st.touch_ball = False
            return finish(1 - team)
        if st.formula == BLOCK and st.result == 1:
            st.touch_ball = False
        if st.formula == SPIKE:
            last_spike_result = st.result

        # what comes next
        if st.formula == SPIKE:
            over(st)
            attack, crosses, team = st, crosses + 1, 1 - team
            formula, link = (BLOCK, 5) if rng.random() < BLOCK_CHANCE else (DIG_SPIKE, 2)
        elif st.formula == BLOCK:
            if st.result == 3:  # stuff block: back to the attackers, who must save it
                over(st)
                attack, team, formula, link = st, 1 - team, SAVE, 2
            else:  # no touch / touch: the blockers dig the spike
                formula, link = DIG_SPIKE, 2
        else:
            nxt_link = 3 if st.link == 2 else st.link + 1
            nxt = next_formula(st.formula, st.result, nxt_link)
            over_link = next_formula(st.formula, st.result, 2) if nxt is None else None
            if nxt is not None and not (st.formula == SAVE and st.link == 4):
                formula, link = nxt, nxt_link
                if nxt == SPIKE:
                    link = 4
            else:  # the ball goes over: free ball (DigNoAttack) or an attack-like save
                over(st)
                attack, crosses, team = st, crosses + 1, 1 - team
                if st.result == 2 or over_link == DIG_NO_ATTACK or st.result in (1,):
                    formula, link = DIG_NO_ATTACK, 2
                else:
                    formula, link = (BLOCK, 5) if rng.random() < 0.5 else (DIG_SPIKE, 2)
                    last_spike_result = min(max(st.result, 3), 6)
            if st.formula in (DIG_SERVE, DIG_SPIKE) and st.result == 2 and not st.over:  # shanked straight over
                over(st)
                attack, crosses, team = st, crosses + 1, 1 - team
                formula, link = DIG_NO_ATTACK, 2
        prev = st
