"""
Builds a rally's FrameData stream from a list of touches (engine stage 1b).

The client only renders what the server sends (decoded/analysis/frame_format.md): for every touch an
announcement (round_data.action_data + events), the toucher's run (MOVE_CURVE) and animation
(ANIMATION_CURVE), the hit (round_data.hit_data on hit_frame) and the ball flight (FLY_CURVE, an exact
parabola in a vertical plane) from hit_frame+1. 15 frames per second; curve frames are match-global.
Timings, animation names and heights are taken from the captured Garena rallies.
"""
import json
import math
from pathlib import Path
import random
from dataclasses import dataclass, field

import gameconfig
import matches

P = matches.P
FPS = 15
GRAVITY = 9.8
HOST, GUEST = 0, 1
TEAM_IDS = 13  # player ids: home 0..12, away 13..25
JOB_SETTER, JOB_LIBERO = 3, 4

# formula_type: (hit_type, link, action_id, default skill, animation, anim lead frames, anim frames, hit height)
SERVE, DIG_SERVE, SAVE, SET, SPIKE, BLOCK, DIG_SPIKE, DIG_NO_ATTACK = range(1, 9)
ACTIONS = {
    SERVE: (0, 1, 100200, 10020, "powerJumpServe_f_ordinary", 24, 40, 3.0),
    DIG_SERVE: (1, 2, 101000, 10100, "catch_fast_medium_near_front_stable", 5, 17, 0.8),
    SET: (2, 3, 102000, 10200, "set_situ_jump_overhead_front_smallpower_slow", 7, 20, 2.4),
    SPIKE: (3, 4, 103000, 10300, "spike_runnear_smash_farnet_positive", 12, 30, 3.1),
    BLOCK: (4, 5, 105000, 10500, "block_palmpress_high_rightjump2_notouch", 8, 22, 3.08),
    DIG_SPIKE: (1, 2, 106000, 10600, "catch_fast_medium_near_front_stable", 5, 17, 0.9),
    SAVE: (5, 2, 108000, 10800, "catch_fast_medium_middle_rightfront_Instability", 5, 17, 0.6),
    DIG_NO_ATTACK: (1, 2, 107000, 10700, "catch_slow_high_middle_front_stable", 5, 17, 1.8),
}
# animation names seen in the captures per (formula, kind)
ANIMS = {
    (SERVE, "ok"): ["powerJumpServe_f_ordinary", "powerJumpServe_r_ordinary", "powerJumpServe_l_ordinary"],
    (DIG_SERVE, "ok"): ["catch_fast_medium_near_right_stable", "catch_fast_slow_near_rightfront_stable",
                        "catch_fast_slow_middle_right_stable", "catch_fast_medium_near_front_stable"],
    (DIG_SPIKE, "ok"): ["catch_fast_medium_near_front_stable", "catch_fast_slow_middle_front_stable",
                        "catch_fast_medium_middle_front_Instability", "catch_fast_medium_near_right_stable"],
    (DIG_SPIKE, "fail"): ["catchfail_toofar_front_slow", "catchfail_toofast2_rightfront_near",
                          "catchfail_leftbehind_medium"],
    (DIG_SERVE, "fail"): ["catchfail_leftbehind_medium", "catchfail_rightbehind_medium"],
    (SAVE, "fail"): ["catchfail_rightbehind_medium", "catchfail_toofast1_rightfront_near", "save_front_dive"],
    (SAVE, "ok"): ["catch_fast_medium_middle_rightfront_Instability", "catch_fast_slow_middle_front_Instability"],
    (SAVE, "set"): ["save_front_situcatch", "set_situ_jump_overhead_right_smallpower_slow"],
    (SET, "ok"): ["set_situ_jump_overhead_front_smallpower_slow", "set_runjump_overhead_left_greatpower",
                  "set_situ_ordinary_overhead_right_greatpower_slow"],
    (SPIKE, "ok"): ["spike_runnear_smash_farnet_positive", "spike_runfar_smash_farnet_positive",
                    "spike_runfar_smash_farnet_rightsideways"],
    (BLOCK, "notouch"): ["block_palmpress_high_rightjump2_notouch", "block_palmpress_high_leftjump2_notouch"],
    (BLOCK, "touch"): ["block_palmpress_high_rightjump2_onetouch", "block_palmpress_high_leftjump2_onetouch"],
    (BLOCK, "stuff"): ["block_palmpress_high_leftjump2_blockback", "block_palmpress_high_rightjump3_blockback"],
    (DIG_NO_ATTACK, "ok"): ["catch_slow_high_middle_front_stable", "catch_slow_high_middle_leftfront_stable"],
}
FAIL_ANIM = "catchfail_leftbehind_medium"
SERVE_READY = ("serve_ready_5", 45)
# frames from a hit to the next hit, and the ball's physical flight time (s) for that pass
FLIGHT = {SERVE: (11, 1.2), DIG_SERVE: (21, 1.5), DIG_SPIKE: (24, 1.5), SET: (18, 1.2), SPIKE: (8, 0.45)}
# round_data.events on the announcement of the next touch, by that touch's formula
ANNOUNCE_EVENTS = {DIG_SERVE: [7, 8], SET: [10, 12], SPIKE: [15, 16], BLOCK: [20], DIG_SPIKE: [19, 8],
                   DIG_NO_ATTACK: [10, 8]}
SAVE_EVENTS = {2: [19, 8], 3: [10, 12], 4: [15, 8]}  # save announcement by link
START_EVENTS = {DIG_SERVE: [9], DIG_SPIKE: [9], SET: [13], DIG_NO_ATTACK: [9]}  # toucher's animation starts

# rotation slots 1..6 (stance_info order) for the home side (z < 0, facing +z, right = -x)
SLOTS = [(-2.8, -6.5), (-2.8, -2.0), (0.0, -2.0), (2.8, -2.0), (2.8, -6.5), (0.0, -7.0)]
SERVER_SPOT = (-2.5, -13.5)
FRONT = (1, 2, 3)  # stance indexes of the front row


def side(team: int) -> int:
    """z sign of a team's half."""
    return -1 if team == HOST else 1


def team_of(pid: int) -> int:
    return HOST if pid < TEAM_IDS else GUEST


@dataclass
class Touch:
    formula: int
    player: int
    result: int
    pos: tuple = (0.0, 0.0, 0.0)  # ball contact point
    touch_ball: bool = True
    frame: int = 0  # local hit frame
    anim: str = ""
    link: int = 0  # 0 = the formula's usual link
    power: float = 0.0  # hit_ball_power
    crit: bool = False
    power_des: str = ""
    over: bool = False  # sends the ball over the net
    skill_id: int = 0  # skill used for the touch (0 = default action skill)
    original: int = -1  # original_hit_result when a buff changed the result
    casts: list = field(default_factory=list)  # skills.Cast shown with this touch's announcement
    buffs: list = field(default_factory=list)  # skills.BuffChange shown with it
    window: tuple | None = None  # (index, "spike"/"defense", [(player, skill)]) ultimate choices
    window_frame: int = 0  # local frame the window was sent in (set by build)
    morale_ready: bool = False
    morale_cast: bool = False
    morale_team: int | None = None
    stand: tuple | None = None  # (x, z) the toucher stands on when his animation starts


@dataclass
class Court:
    """Who stands where: stance lists (6 ids each, slot order) and the card jobs."""
    stance: dict  # team -> [6 player ids]
    jobs: dict  # player id -> job
    pos: dict = field(default_factory=dict)  # player id -> (x, z)
    home: dict = field(default_factory=dict)  # player id -> (x, z) rotation spot he returns to
    plan: dict = field(default_factory=dict)  # player id -> his runs and animations in time order (_claim)
    g: int = 0  # global frame offset of the rally being built

    def setter(self, team: int) -> int:
        ids = self.stance[team]
        return next((p for p in ids if self.jobs.get(p) == JOB_SETTER), ids[2])

    def front(self, team: int) -> list[int]:
        return [self.stance[team][i] for i in FRONT]

    def back(self, team: int) -> list[int]:
        return [p for i, p in enumerate(self.stance[team]) if i not in FRONT]

    def nearest(self, ids, x: float, z: float) -> int:
        return min(ids, key=lambda p: (self.pos[p][0] - x) ** 2 + (self.pos[p][1] - z) ** 2)


def court_for(info, stance: dict) -> Court:
    jobs = {}
    for team, t in ((HOST, info.home), (GUEST, info.away)):
        for i, p in enumerate(t.formation.players):
            if p.info.conf_id:
                jobs[team * TEAM_IDS + i] = gameconfig.PLAYERS.get(p.info.conf_id, {}).get("Job")
    return Court(stance, jobs)


# ---------------------------------------------------------------- touch plan (placeholder logic)

ANIM_TABLE_PATH = Path(__file__).parent / "data" / "anim_table.json"
ANIM_TABLE = json.loads(ANIM_TABLE_PATH.read_text()) if ANIM_TABLE_PATH.exists() else {}
MIN_SAMPLES = 3
# contact geometry when an animation was never measured (formula -> dx, dz, y)
DEFAULT_GEOM = {SERVE: (0.0, 4.1, 2.9), DIG_SERVE: (0.2, 0.5, 0.8), DIG_SPIKE: (0.1, 0.6, 0.8), SAVE: (0.3, 0.6, 0.7),
                SET: (0.0, 0.1, 2.6), SPIKE: (0.1, 3.4, 3.0), BLOCK: (0.0, 0.67, 3.2), DIG_NO_ATTACK: (0.0, 0.5, 1.8)}
# animation pools (name prefixes) per (formula, kind); only measured animations are used
POOLS = {
    (SERVE, "ok"): ("powerJumpServe",), (SET, "ok"): ("set_",), (SAVE, "set"): ("set_",),
    (SPIKE, "ok"): ("spike_",), (SPIKE, "quick"): ("quickspike_",), (SAVE, "attack"): ("spike_",),
    (DIG_SERVE, "ok"): ("catch_fast",), (DIG_SPIKE, "ok"): ("catch_fast",), (SAVE, "ok"): ("catch_",),
    (DIG_NO_ATTACK, "ok"): ("catch_slow",), (BLOCK, "touch"): ("block_",), (BLOCK, "stuff"): ("block_",),
}


def _measured(prefixes, word=None) -> list[str]:
    return [n for n, e in ANIM_TABLE.items() if e["n"] >= MIN_SAMPLES and n.startswith(prefixes)
            and (word is None or word in n)]


def _anim_for(f, kind, rng) -> str:
    if kind == "fail":
        return rng.choice(ANIMS.get((f, "fail")) or [FAIL_ANIM])
    if kind == "scramble":
        names = [n for n in ("save_front_situcatch", "save_foot") if n in ANIM_TABLE]
        return rng.choice(names) if names else rng.choice(ANIMS[(SAVE, "ok")])
    if f == BLOCK and kind == "notouch":
        return rng.choice(ANIMS[(BLOCK, "notouch")])
    word = {"touch": "onetouch", "stuff": "blockback"}.get(kind) if f == BLOCK else None
    names = _measured(POOLS.get((f, kind), ()), word) if (f, kind) in POOLS else []
    return rng.choice(names) if names else rng.choice(ANIMS.get((f, kind)) or ANIMS.get((f, "ok")) or [ACTIONS[f][4]])


def geometry(anim: str, formula: int) -> tuple[float, float, float, int | None, int | None]:
    """(dx, dz, y, lead, dur) of an animation: where the ball meets the player relative to his spot at
    animation start, and its timing (data/anim_table.json, measured from Garena's rallies)."""
    e = ANIM_TABLE.get(anim)
    if (e is None or "dx" not in e) and "_notouch" in anim:  # no-touch blocks: same jump as the touched ones
        twin = (ANIM_TABLE.get(anim.replace("_notouch", "_blockback"))
                or ANIM_TABLE.get(anim.replace("_notouch", "_onetouch")))
        if twin and "dx" in twin:
            return twin["dx"], twin["dz"], twin["y"], twin.get("lead"), (e or twin).get("dur")
    if e and "dx" in e:
        return e["dx"], e["dz"], e["y"], e.get("lead"), e.get("dur")
    dx, dz, y = DEFAULT_GEOM.get(formula, (0.0, 0.5, 1.0))
    return dx, dz, y, None, None


def end_offset(anim: str) -> tuple[float, float]:
    """Root motion of an animation in the team frame: where the player stands when it is over,
    relative to where it started (a spike carries him 4-5.5 m forward, a block jump 1-2.7 m sideways)."""
    e = ANIM_TABLE.get(anim) or {}
    return e.get("ex", 0.0), e.get("ez", 0.0)


def stand_for(contact: tuple, anim: str, formula: int, team: int) -> tuple[float, float]:
    """Where the player must stand at animation start so the animation brings his hands to contact."""
    dx, dz, *_ = geometry(anim, formula)
    face = 1 if team == HOST else -1  # home faces +z
    return contact[0] - dx * face, contact[2] - dz * face


def touches_from(court: Court, serve_team: int, steps: list, rng=random) -> list[Touch]:
    """Place the engine's touch chain (engine.simulate) on the court: contact points, animations and the
    spot each toucher runs to. Contact points follow Garena's captured rallies (spikes at the wings about
    2.4 m from the net, sets 2 m, blocks at the net, receives 4.5-6.5 m deep)."""
    out = []
    last_attack_x = 0.0
    for st in steps:
        team = team_of(st.player)
        t = side(team)
        f, link = st.formula, st.link
        kind = "ok"
        if f == SERVE:  # contact = the server's spot behind the baseline + his animation's reach
            x, z = SERVER_SPOT[0] * -t, SERVER_SPOT[1] * -t
        elif f == SAVE and link in (3, 4) and st.result <= 2:
            # a scrambled save (the ball deviates again or just goes over as a free ball): Garena plays
            # its save animations, chasing the ball away from the net - not a set or a spike
            x, z = rng.uniform(-3.0, 3.0), t * rng.uniform(4.5, 6.5)
            kind = "scramble"
        elif f == SET or (f == SAVE and link == 3):
            x, z = rng.uniform(-1.2, 1.2), t * rng.uniform(1.7, 2.3)
            kind = "ok" if f == SET else "set"
        elif f == SPIKE or (f == SAVE and link == 4):
            quick = court.jobs.get(st.player) == 2
            sx = court.pos.get(st.player, (0.0, 0.0))[0]
            last_attack_x = (rng.uniform(-0.8, 0.8) if quick
                             else (1 if sx >= 0 else -1) * rng.uniform(1.6, 2.8))
            x, z = last_attack_x, t * rng.uniform(2.0, 2.8)
            kind = ("quick" if quick and _measured(POOLS[(SPIKE, "quick")]) else "ok") if f == SPIKE else "attack"
        elif f == BLOCK:
            x, z = last_attack_x, t * 0.2
            kind = {1: "notouch", 2: "touch", 3: "stuff", 0: "stuff"}[st.result]
        else:  # receive / dig / save at link 2 / free ball
            x, z = rng.uniform(-3.0, 3.0), t * rng.uniform(4.4, 6.5)
        if not st.touch_ball and f != BLOCK:
            kind = "fail"
        anim = _anim_for(f, kind, rng)
        if f not in (SERVE, BLOCK) and kind != "fail":  # the animation must end on the own side of the net
            need = 0.4 + end_offset(anim)[1] - geometry(anim, f)[1]
            if abs(z) < need:
                z = t * need
        y = 0.0 if kind == "fail" else geometry(anim, f)[2]
        if f == SERVE:
            dx, dz = geometry(anim, f)[:2]
            face = 1 if team == HOST else -1
            stand = (x, z)
            pos = (x + dx * face, y, z + dz * face)
        else:
            pos = (x, y, z)
            stand = (x, z) if kind == "fail" else stand_for(pos, anim, f, team)
        tc = Touch(f, st.player, st.result, pos, touch_ball=st.touch_ball, anim=anim,
                   link=link, power=st.power.ball, crit=st.power.crit, power_des=st.power.des,
                   over=st.over, skill_id=getattr(st, "skill_id", 0), original=getattr(st, "original", -1),
                   casts=getattr(st, "casts", []), buffs=getattr(st, "buffs", []),
                   window=getattr(st, "window", None), morale_ready=getattr(st, "morale_ready", False),
                   morale_cast=getattr(st, "morale_cast", False), morale_team=getattr(st, "morale_team", None))
        tc.stand = stand
        out.append(tc)
    return out


# ---------------------------------------------------------------- frame building

INT_MIN = -2147483648


def _skills(f, casts, buffs):
    """skill_data.casting_skills and buff_data.changed_buffs (decoded/analysis/frame_format.md 4.1/4.2)."""
    for c in casts:
        f.skill_data.casting_skills.add(skill_id=c.skill_id, player_id=c.player, skill_lv=c.level)
    for b in buffs:
        if b.entity[0] == "e":  # stamina: the bar under the player's card follows these
            pc = f.property_data.changed_property.add(player_id=b.entity[1], team_id=INT_MIN, buff_id=INT_MIN)
            pc.attrs.add(type=8, val=b.count, base_val=b.count)
            continue
        info = f.buff_data.changed_buffs.add(buff_id=b.buff_id, count=b.count, cd=b.cd)
        info.target.entity_type = 0 if b.entity[0] == "p" else 1
        info.target.entity_id = b.entity[1]


def _extras(fr, t: Touch, ann: int):
    """Ultimate window and morale skill state carried by touch t (frame_format.md 4.1 / 6)."""
    if t.window:
        _, kind, opts = t.window
        frame = ann if kind == "spike" else t.frame  # attack window with our set call, defence at their set
        t.window_frame = frame
        for pid, sid in opts:
            fr(frame).skill_data.ultimate_skills.add(player_id=pid, skill_id=sid)
    if t.morale_team is not None:
        td = fr(ann).team_data.add()
        td.team_id = t.morale_team
        td.morale_skill.state = P.Released if hasattr(P, "Released") else 2
        td.morale_skill.skill_id, td.morale_skill.cd, td.morale_skill.cd_total_time = 300, 8, 8
        fr(ann).skill_data.casted_team_skills.add(skill_id=300, player_id=t.morale_team * 13, is_team=True)
    if t.morale_ready:
        frame = t.frame
        td = fr(frame).team_data.add()
        td.morale_skill.state = P.Actived if hasattr(P, "Actived") else 1
        td.morale_skill.skill_id = 300
        fr(frame).skill_data.team_skills.add(skill_id=300, is_team=True)


def _flight(src: Touch) -> tuple[int, float]:
    """(frames to the next hit, physical flight seconds) for a ball leaving src."""
    if src.formula == BLOCK:
        return 12, 0.9
    if src.over and src.formula != SPIKE and src.formula != SERVE:  # free ball / save over the net
        return 24, 1.6
    if src.formula == SAVE:
        return {2: (21, 1.5), 3: (18, 1.2)}.get(src.link or 2, (22, 1.4))
    if src.formula == DIG_NO_ATTACK:
        return 21, 1.5
    return FLIGHT.get(src.formula, (20, 1.4))


def _vec(m, x=0.0, y=0.0, z=0.0):
    m.x, m.y, m.z = x, y, z


def _hit_data(h, t: Touch):
    hit_type, link, action, skill, anim, *_ = ACTIONS.get(t.formula, ACTIONS[DIG_SPIKE])
    link = t.link or link
    h.hit_player = t.player
    h.hit_type = hit_type
    h.hit_result = t.result
    h.skill_id = t.skill_id or skill
    h.hit_frame = t.frame
    h.touch_ball = t.touch_ball
    if t.touch_ball:
        _vec(h.hit_ball_pos, *t.pos)
    h.formula_type = t.formula
    h.link = link
    h.hit_anim = t.anim or anim
    h.action_id = action
    h.original_hit_result = t.original
    h.hit_ball_power = t.power
    h.crit = t.crit
    if t.power_des:
        h.power_des = t.power_des


NET_CLEAR = 2.58
BALL_RADIUS = 0.1
# The rally ends one frame after the last ball curve, as in Garena's rallies: the client lets the ball
# bounce by itself from then on. With a later end the ball lay still on the floor until the end came.
END_AFTER = 1


def _ball_curve(f, start: tuple, end: tuple, g0: int, g1: int, flight_s: float):
    """FLY_CURVE from start to end (world x,y,z) between global frames g0..g1."""
    dx, dz = end[0] - start[0], end[2] - start[2]
    d = max(math.hypot(dx, dz), 0.01)
    ux, uz = dx / d, dz / d
    c = start[1]
    while True:
        vx = d / flight_s
        a = -GRAVITY / (2 * vx * vx)
        b = (end[1] - c - a * d * d) / d
        if start[2] * end[2] >= 0 or abs(uz) < 1e-6 or flight_s > 4:
            break
        dn = -start[2] / uz  # distance to the net plane: the ball must clear the tape (2.43 m + ball)
        if a * dn * dn + b * dn + c >= NET_CLEAR:
            break
        flight_s *= 1.06  # a slower, higher arc (a hard spike hit far from the net went through the net)
    vy = b * vx
    b_ = f.ball_data
    _vec(b_.position, *start)
    b_.state = P.BALL_FLY if hasattr(P, "BALL_FLY") else 1
    cd = b_.curve_data
    cd.start_frame, cd.end_frame = g0, g1
    cd.curve_type = P.FLY_CURVE if hasattr(P, "FLY_CURVE") else 1
    fc = cd.fly_curve_data
    _vec(fc.start_velocity3_d, ux * vx, vy, uz * vx)
    fc.start_velocity2_d.x, fc.start_velocity2_d.y = vx, vy
    fc.time_zoom = flight_s / max((g1 - g0) / FPS, 1 / FPS)
    fc.start_pos2_d.y = c
    fc.end_pos2_d.x, fc.end_pos2_d.y = d, end[1]
    m = fc.plane_to_world
    m.c0.x, m.c0.z = ux, uz
    m.c1.y = 1.0
    m.c2.x, m.c2.z = -uz, ux
    m.c3.x, m.c3.z, m.c3.w = start[0], start[2], 1.0
    fc.a, fc.b, fc.c = a, b, c


MOVE_TABLE_PATH = Path(__file__).parent / "data" / "move_table.json"
MOVE_TABLE = json.loads(MOVE_TABLE_PATH.read_text()) if MOVE_TABLE_PATH.exists() else {"types": {}, "speed": {}}
RUN = 2  # plain run (5 m/s in Garena's rallies)


def _move_type(dx: float, dz: float) -> tuple[int, float]:
    """Garena's locomotion style and its speed for a run of (dx, dz) in the player's frame (dz = towards
    the net): the style Garena used most for that direction and distance (data/move_table.json: small
    steps 0.8 m/s, side shuffles 2.5, backpedal 3.3-3.5, run 5 ...). Every style is an animation made
    for its own speed; played at another speed the player glides over the floor."""
    cell = MOVE_TABLE.get("cell", 0.5)
    cx, cz = round(dx / cell), round(dz / cell)
    types = MOVE_TABLE["types"]
    kind = types.get(f"{cx},{cz}")
    if kind is None:  # nearest measured cell
        best = None
        for key, t in types.items():
            kx, kz = map(int, key.split(","))
            d = (kx - cx) ** 2 + (kz - cz) ** 2
            if best is None or d < best[0]:
                best = (d, t)
        kind = best[1] if best else RUN
    return kind, MOVE_TABLE["speed"].get(str(kind), 4.0)


def _drop(entries):
    for frame, entry in entries:
        for i, e in enumerate(frame.player_data):
            if e is entry or e == entry:
                del frame.player_data[i]
                break


def _claim(court: Court, pid: int, local: int, run: bool) -> int:
    """A new run or animation for a player from local frame `local`: what was planned for him from then
    on is dropped (his way back, a team shape he no longer takes), a run he is still on is cut where he
    is, and a run waits until his current animation is over. Returns the frame the command starts.
    Without this a standby sent for the old run broke the new animation and players jumped to spots
    they never reached."""
    items = court.plan.setdefault(pid, [])
    while items and items[-1]["s"] >= local:
        it = items.pop()
        _drop(it["entries"])
        if run:
            court.pos[pid] = it["p0"]
    if items and items[-1]["e"] > local:
        it = items[-1]
        if it["kind"] == "anim":
            if run:
                local = it["e"]
        else:
            _drop(it["entries"][1:])  # his standby at the old target
            it["entries"] = it["entries"][:1]
            if run:
                court.pos[pid] = _where(it, local)
            it["e"] = local
    return local


def _where(it: dict, local: int) -> tuple[float, float]:
    """Where a player on a run is at a local frame (distance from the run's speed profile)."""
    v, acc, dec, t1, t2, t3 = it["prof"]
    t = max(0.0, min((local - it["s"]) / FPS, t3))
    if t <= t1:
        d = acc * t * t / 2
    elif t <= t2:
        d = v * t1 / 2 + v * (t - t1)
    else:
        d = v * t1 / 2 + v * (t2 - t1) + v * (t - t2) - dec * (t - t2) ** 2 / 2
    u = max(0.0, min(d / it["dist"], 1.0)) if it["dist"] else 1.0
    (x0, z0), (cx, cz), (x1, z1) = it["p0"], it["c"], it["p1"]
    return ((1 - u) ** 2 * x0 + 2 * u * (1 - u) * cx + u * u * x1,
            (1 - u) ** 2 * z0 + 2 * u * (1 - u) * cz + u * u * z1)


def _profile(dist: float, style_v: float, t_avail: float) -> tuple[float, float, float, bool]:
    """(top speed, acceleration, braking, style kept) of a run over dist that is over within t_avail:
    the locomotion style's own speed when that is fast enough, else a run as fast as needed (Garena's
    runs stay under 9 m/s); only when even that is too slow he starts and stops harder."""
    for k in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0):
        acc, dec = 15.0 * k, 10.0 * k
        c = 1 / (2 * acc) + 1 / (2 * dec)
        peak = math.sqrt(dist / c)  # top speed of a run that only speeds up and brakes
        v = min(style_v, peak)
        if k == 1.0 and dist / v + v * c <= t_avail * 1.02:
            return v, acc, dec, True
        disc = t_avail * t_avail - 4 * c * dist
        if disc >= 0:
            v = (t_avail - math.sqrt(disc)) / (2 * c)
            if v <= MAX_RUN:
                return max(v, 0.5), acc, dec, False
    return MAX_RUN, acc, dec, False


def _move(fr, court: Court, pid: int, target: tuple, local: int, frames_avail: int, state: int | None = None):
    """MOVE_CURVE from the player's spot to target (x, z) from local frame `local`, done within
    frames_avail frames. With a state, a standby (NONE_CURVE, that stance) follows when he arrives, as
    Garena sends it after nearly every run; without it the client keeps the running pose."""
    want = local
    local = _claim(court, pid, local, True)
    frames_avail -= local - want
    x0, z0 = court.pos[pid]
    x1, z1 = target
    dist = math.hypot(x1 - x0, z1 - z0)
    court.pos[pid] = (x1, z1)
    if dist < 0.05:
        return 0
    fwd = 1.0 if team_of(pid) == HOST else -1.0
    kind, style_v = _move_type((x1 - x0) * fwd, (z1 - z0) * fwd)
    v, acc, dec, style = _profile(dist, style_v, max(frames_avail, 2) / FPS)
    if not style:
        kind = RUN
    t1 = v / acc
    t2 = t1 + max(dist - v * (v / (2 * acc) + v / (2 * dec)), 0) / v
    t3 = t2 + v / dec
    frames = max(round(t3 * FPS), 1)
    g0 = court.g + local
    f = fr(local)
    p = f.player_data.add()
    p.player_id = pid
    p.state = P.PLAYER_MOVE
    _vec(p.position, x0, 0.0, z0)
    cd = p.curve_data
    cd.start_frame, cd.end_frame = g0, g0 + frames
    cd.curve_type = P.MOVE_CURVE
    mc = cd.move_curve_data
    mc.bezier.p0.x, mc.bezier.p0.y = x0, z0
    mc.bezier.p1.x, mc.bezier.p1.y = x1, z1
    if kind == RUN:  # a real run leaves forwards first, like Garena's curves
        mc.bezier.c0.x, mc.bezier.c0.y = x0, z0 + 0.5 * fwd
    else:  # steps and shuffles go straight
        mc.bezier.c0.x, mc.bezier.c0.y = x0 + (x1 - x0) * 0.02, z0 + (z1 - z0) * 0.02
    for time, value, tin, tout in ((0, 0, 0, acc), (t1, v, acc, 0), (t2, v, 0, -dec), (t3, 0, -dec, 0)):
        k = mc.velocity_curve.frames.add()
        k.time, k.value, k.in_tangent, k.out_tangent = time, value, tin, tout
    mc.move_type = kind
    mc.end_forward.z = fwd
    entries = [(f, p)]
    if state is not None:
        sf = fr(local + frames)
        sb = sf.player_data.add()
        sb.player_id = pid
        sb.state = state
        _vec(sb.position, x1, 0.0, z1)
        sb.forward.z = fwd
        sb.curve_data.curve_type = P.NONE_CURVE
        sb.curve_data.standby_curve_data.state = state
        entries.append((sf, sb))
    court.plan[pid].append({"kind": "run", "s": local, "e": local + frames, "entries": entries,
                            "p0": (x0, z0), "p1": (x1, z1), "c": (mc.bezier.c0.x, mc.bezier.c0.y),
                            "dist": dist, "prof": (v, acc, dec, t1, t2, t3)})
    return frames


def anim_forward(name: str) -> tuple[float, float]:
    """Facing of an animation in the team frame (home: +z = towards the net), as Garena sent it with
    that animation (setters face their hitter, not the net); the root motion follows this facing."""
    e = ANIM_TABLE.get(name)
    if e is None and "_notouch" in name:
        e = ANIM_TABLE.get(name.replace("_notouch", "_blockback")) or ANIM_TABLE.get(name.replace("_notouch", "_onetouch"))
    if e and "fx" in e:
        return e["fx"], e["fz"]
    return 0.0, 1.0


def _anim(f, court: Court, pid: int, name: str, g0: int, frames: int):
    local = g0 - court.g
    _claim(court, pid, local, False)
    x, z = court.pos[pid]
    p = f.player_data.add()
    p.player_id = pid
    p.state = P.PLAYER_MOVE
    _vec(p.position, x, 0.0, z)
    fx, fz = anim_forward(name)
    face = 1.0 if team_of(pid) == HOST else -1.0
    p.forward.x, p.forward.z = fx * face, fz * face
    ex, ez = end_offset(name)
    court.pos[pid] = (x + ex * face, z + ez * face)
    cd = p.curve_data
    cd.start_frame, cd.end_frame = g0, g0 + frames
    cd.anim_curve_data.anim_name = name
    cd.anim_curve_data.total_time = frames / FPS
    court.plan[pid].append({"kind": "anim", "s": local, "e": local + frames, "entries": [(f, p)],
                            "p0": (x, z), "p1": court.pos[pid]})


MAX_RUN = 8.5  # m/s, Garena's fastest captured runs
# team shapes for the players who are not touching, in home coordinates (z < 0, net at 0); away mirrors x and z
SHAPES = {
    "receive": {"back": [(-3.0, -6.2), (0.0, -6.8), (3.0, -6.2)], "front": [(-2.8, -3.6), (0.0, -3.0), (2.8, -3.6)],
                "setter": (0.8, -1.6)},
    "attack": {"back": [(-2.8, -5.6), (0.0, -6.4), (2.8, -5.6)], "front": [(-3.0, -4.0), (0.0, -3.4), (3.0, -4.0)],
               "setter": (0.6, -1.8)},
    "defend": {"back": [(-3.0, -6.6), (0.0, -7.6), (3.0, -6.6)], "front": [(-2.6, -0.8), (0.0, -0.8), (2.6, -0.8)],
               "setter": None},
    "cover": {"back": [(-2.6, -5.8), (0.0, -6.8), (2.6, -5.8)], "front": [(-2.4, -2.2), (0.0, -2.0), (2.4, -2.2)],
              "setter": None},
}


# stance a player takes when he arrives (standby_curve_data.state; Garena's most used: precatch_spike 6,
# preblock 4, precatch_block 7, prespike 5, precatch 2, preset 3)
STANCE = {
    "receive": {"front": P.PLAYER_PRESPIKE, "back": P.PLAYER_PRECATCH, "setter": P.PLAYER_PRESET},
    "attack": {"front": P.PLAYER_PRESPIKE, "back": P.PLAYER_PRECATCH_BLOCK, "setter": P.PLAYER_PRESET},
    "defend": {"front": P.PLAYER_PREBLOCK, "back": P.PLAYER_PRECATCH_SPIKE, "setter": P.PLAYER_PREBLOCK},
    "cover": {"front": P.PLAYER_PRECATCH_BLOCK, "back": P.PLAYER_PRECATCH_BLOCK, "setter": P.PLAYER_PRECATCH_BLOCK},
}
TOUCH_STANCE = {DIG_SERVE: P.PLAYER_PRECATCH, DIG_SPIKE: P.PLAYER_PRECATCH_SPIKE, DIG_NO_ATTACK: P.PLAYER_PRECATCH,
                SAVE: P.PLAYER_SAVEPROTECT, SET: P.PLAYER_PRESET, SPIKE: P.PLAYER_PRESPIKE, BLOCK: P.PLAYER_PREBLOCK}


def _formation(fr, court: Court, team: int, phase: str, frame: int, g: int, frames_avail: int,
               skip: set, busy: dict):
    """Moves every non-touching player of a team into the phase's shape (Garena keeps all players moving:
    ~90 runs per rally in the captures). Front/back row keep their order from left to right."""
    shape = SHAPES[phase]
    s = side(team)  # home -1: shapes are written for home, so mirror with -s
    setter = court.setter(team)
    for row, ids in (("front", court.front(team)), ("back", court.back(team))):
        spots = list(shape[row])
        order = sorted(ids, key=lambda p: court.pos.get(p, (0, 0))[0] * -s)
        for pid, (x, z) in zip(order, spots):
            if pid in skip or busy.get(pid, 0) > frame:
                continue
            if pid == setter and shape["setter"]:
                x, z = shape["setter"]
            target = (x * -s, z * -s)
            cur = court.pos.get(pid)
            if cur and math.hypot(cur[0] - target[0], cur[1] - target[1]) > 0.4:
                state = STANCE[phase]["setter" if pid == setter and shape["setter"] else row]
                _move(fr, court, pid, target, frame, max(frames_avail, 8), state)


def _return(fr, court: Court, pid: int, local: int, g: int, state: int | None = None):
    """After an animation the player walks back to his rotation spot (Garena sends this run one frame
    after nearly every animation). A later run that starts earlier replaces it."""
    here = court.pos[pid]
    home = court.home.get(pid)
    if not home or math.hypot(here[0] - home[0], here[1] - home[1]) < 0.3:
        return
    _move(fr, court, pid, home, local, 30, state if state is not None else P.PLAYER_PRECATCH_BLOCK)


def _feint(fr, court: Court, t: Touch, lead: int, g: int, busy: dict, prev_announce: int, exclude=()):
    """A front-row teammate of the spiker runs a fake attack (Garena: about 0.7 feints per spike)."""
    team = team_of(t.player)
    face = 1 if team == HOST else -1
    name = "feignattack_runnear_swipearm"
    e = ANIM_TABLE.get(name)
    if not e:
        return
    start = t.frame - lead - 2
    setter = court.setter(team)
    pool = [p for p in court.front(team) if p not in (t.player, setter) and court.jobs.get(p) != JOB_LIBERO
            and busy.get(p, 0) <= prev_announce and p not in exclude]
    if not pool or start - prev_announce < 6:
        return
    pid = max(pool, key=lambda p: abs(court.pos[p][0] - t.pos[0]))  # the one away from the real attack
    lane = max(-3.6, min(3.6, court.pos[pid][0]))
    dist = 1.0 + e["ez"]  # he lands about 1 m from the net
    spot = (lane - e.get("ex", 0.0) * face, -dist * face)
    _move(fr, court, pid, spot, prev_announce, start - prev_announce)
    dur = int(e.get("dur") or 26)
    _anim(fr(start), court, pid, name, g + start, dur)
    busy[pid] = start + dur
    _return(fr, court, pid, start + dur + 1, g)


def _assist_block(fr, court: Court, t: Touch, ann: int, lead: int, dur: int, g: int, busy: dict, exclude=()):
    """Front-row teammates next to the blocker jump with him (Garena's blocks are mostly two or three
    players); they close in beside him and play a no-touch block jump at the same moment."""
    team = team_of(t.player)
    stand = t.stand or (t.pos[0], t.pos[2])
    mates = [p for p in court.front(team) if p != t.player and court.jobs.get(p) != JOB_LIBERO
             and busy.get(p, 0) <= ann and p not in exclude
             and abs(court.pos.get(p, (99, 0))[0] - stand[0]) < 3.2]
    mates.sort(key=lambda p: abs(court.pos[p][0] - stand[0]))
    face = 1 if team == HOST else -1
    used = {}
    for pid in mates[:2]:
        side_ = 1 if court.pos[pid][0] >= t.pos[0] else -1
        if side_ in used:  # both on the same side: the second one goes to the other side
            side_ = -side_
        used[side_] = pid
        land_x = t.pos[0] + 0.95 * side_  # he comes down beside the blocker
        # a jump that slides towards the ball from his side (block jumps carry the player 1-2.7 m sideways)
        want = -side_ * face  # sign of the sideways motion in the team frame
        names = [n for n, e in ANIM_TABLE.items() if n.startswith("block_") and n.endswith("_notouch")
                 and e.get("ne", 0) >= 5 and e.get("ex", 0) * want > 0]
        if not names:
            continue
        name = min(names, key=lambda n: abs(ANIM_TABLE[n]["ex"]))
        ex, ez = end_offset(name)
        target = (land_x - ex * face, stand[1])
        start = t.frame - lead
        _move(fr, court, pid, target, ann, max(start - ann, 2))
        adur = int(ANIM_TABLE[name].get("dur") or dur)
        _anim(fr(start), court, pid, name, g + start, adur)
        busy[pid] = start + adur
        _return(fr, court, pid, start + adur + 1, g, P.PLAYER_PREBLOCK)


def rng_choice(names: list, salt: int) -> str:
    return names[salt % len(names)]


def place(court: Court, serve_team: int, libero: dict):
    """Rally start: everyone on their rotation slot, the server behind the baseline, liberos aside."""
    court.pos.clear()
    for team in (HOST, GUEST):
        s = side(team)
        for i, pid in enumerate(court.stance[team]):
            x, z = SLOTS[i]
            court.pos[pid] = (x * -s, z * -s)
        if team == serve_team:
            court.pos[court.stance[team][0]] = (SERVER_SPOT[0] * -s, SERVER_SPOT[1] * -s)
        # a libero who is not in for someone stays off the court: Garena's setup frame only has the
        # six players on court (he used to stand at the side line as a seventh player)


def build(court: Court, serve_team: int, touches: list[Touch], g: int, start: dict,
          skill_start=((), ()), skill_end=((), ())) -> tuple[list, int]:
    """FrameData list for one rally (game/round ids and scores are set by the caller) and its last
    local frame. g = global frame offset of this rally (sum of earlier rallies' last frames);
    start = positions at rally start (from place(), before plan() moved anyone)."""
    court.pos = dict(start)
    court.g, court.plan = g, {}
    court.home = dict(start)
    hx, hz = SLOTS[0]
    court.home[court.stance[serve_team][0]] = (hx * -side(serve_team), hz * -side(serve_team))
    frames: dict[int, P.FrameData] = {}

    def fr(i: int) -> P.FrameData:
        if i not in frames:
            frames[i] = P.FrameData(frame_index=i)
        return frames[i]

    if serve_team == GUEST:
        fr(1).round_data.serve_team = GUEST
    else:
        fr(1)
    setup = fr(2)
    for pid in court.pos:
        setup.player_data.add(player_id=pid, clear_curve=True)
    for pid, (x, z) in court.pos.items():
        p = setup.player_data.add(player_id=pid)
        team = team_of(pid)
        if pid == court.stance[serve_team][0]:
            p.state = P.PLAYER_PRESERVE
        elif pid == court.setter(team):
            p.state = P.PLAYER_PRESET
        _vec(p.position, x, 0.0, z)
        p.forward.z = 1.0 if team == HOST else -1.0
        p.curve_data.curve_type = P.NONE_CURVE
        p.curve_data.standby_curve_data.state = p.state
    setup.round_data.events.append(2)
    _skills(setup, *skill_start)

    # serve
    serve = touches[0]
    ready_name, ready_frames = SERVE_READY
    announce = 3
    _anim(fr(announce), court, serve.player, ready_name, g + announce, ready_frames)
    anim_start = announce + ready_frames
    fr(anim_start).round_data.events.append(5)
    _, _, _, g_lead, g_dur = geometry(serve.anim, SERVE)
    lead, dur = int(g_lead or ACTIONS[SERVE][5]), int(g_dur or ACTIONS[SERVE][6])
    serve.frame = anim_start + lead
    _anim(fr(anim_start + 1), court, serve.player, serve.anim or ACTIONS[SERVE][4], g + anim_start, dur)
    _return(fr, court, serve.player, anim_start + dur + 1, g)
    fr(announce).round_data.events.extend([3, 4])
    _hit_data(fr(announce).round_data.action_data, serve)
    _skills(fr(announce), serve.casts, serve.buffs)
    _extras(fr, serve, announce)
    _hit_data(fr(serve.frame).round_data.hit_data, serve)
    fr(serve.frame - 13).round_data.events.append(6)
    # while the server prepares: his team takes the defence shape, the receivers their receive shape
    _formation(fr, court, serve_team, "cover", anim_start, g, 30, {serve.player}, {})
    _formation(fr, court, 1 - serve_team, "receive", announce + 1, g, 40, set(), {})

    prev, prev_announce = serve, announce
    read_from = 0  # local frame from which the next blocker may move (the set's call)
    busy = {serve.player: serve.frame - lead + dur}  # player -> local frame his last animation ends
    for i, t in enumerate(touches[1:], 1):
        hit_type, link, action, skill, anim, lead, dur, height = ACTIONS[t.formula]
        _, _, _, g_lead, g_dur = geometry(t.anim or anim, t.formula)
        lead, dur = int(g_lead or lead), int(g_dur or dur)
        stand = t.stand or (t.pos[0], t.pos[2])
        if t.formula == BLOCK:  # jumps on the attack: hit 2 frames after it, announced right after its announcement
            t.frame = prev.frame + 2
            ann = prev_announce + 1
            # the blockers read the set: they go from the set's call on (the team shape left them out)
            mv = max(read_from or ann, busy.get(t.player, 0))
            _move(fr, court, t.player, stand, mv, max(t.frame - lead - mv, 2),
                  TOUCH_STANCE.get(t.formula) if t.frame - lead - mv > 6 else None)
            busy[t.player] = t.frame - lead + dur
            _anim(fr(t.frame - lead), court, t.player, t.anim or anim, g + t.frame - lead, dur)
            _return(fr, court, t.player, t.frame - lead + dur + 1, g, P.PLAYER_PREBLOCK)
            later = {u.player for u in touches[i + 1:i + 3]}  # they have to play the ball right after
            _assist_block(fr, court, t, max(read_from or ann, ann - 12), lead, dur, g, busy, later)
            read_from = 0
            fr(ann).round_data.events.extend(ANNOUNCE_EVENTS[BLOCK])
            _hit_data(fr(ann).round_data.action_data, t)
            _skills(fr(ann), t.casts, t.buffs)
            _extras(fr, t, ann)
            _hit_data(fr(t.frame).round_data.hit_data, t)
            prev = t
            continue
        # the ball comes from the last touch that made contact (a block without touch lets it through)
        src = next(u for u in reversed(touches[:i]) if u.touch_ball)
        n, flight = _flight(src)
        t.frame = max(src.frame + n, prev.frame + 6)
        ann = prev.frame + 1
        end = t.pos if t.touch_ball else (t.pos[0], BALL_RADIUS, t.pos[2])
        _ball_curve(fr(src.frame + 1), src.pos, end, g + src.frame, g + t.frame, flight)
        # run as early as the play allows (from the previous touch's call, like Garena's spikers who start
        # their approach while the ball is still on the way to the setter), never during his own animation
        mv = max(prev_announce, busy.get(t.player, 0))
        if t.frame - lead - mv < 4:
            mv = max(ann, busy.get(t.player, 0))
        _move(fr, court, t.player, stand, mv, max(t.frame - lead - mv, 2),
              TOUCH_STANCE.get(t.formula) if t.frame - lead - mv > 6 else None)
        busy[t.player] = t.frame - lead + dur
        nxt = touches[i + 1] if i + 1 < len(touches) else None
        skip = {t.player} | ({nxt.player} if nxt else set())
        blk = touches[i + 2] if i + 2 < len(touches) and touches[i + 2].formula == BLOCK else None
        if blk and t.formula in (SET, SAVE):
            skip.add(blk.player)
            read_from = ann
        span = (nxt.frame if nxt and nxt.frame else t.frame + 20) - ann
        own, other = team_of(t.player), 1 - team_of(t.player)
        if t.formula == SET or (t.formula == SAVE and (t.link or 2) == 3):
            _formation(fr, court, own, "attack", ann, g, t.frame - ann + 10, skip, busy)
            _formation(fr, court, other, "defend", ann, g, t.frame - ann + 14, skip, busy)
        elif t.formula in (DIG_SERVE, DIG_SPIKE, DIG_NO_ATTACK) or (t.formula == SAVE and (t.link or 2) == 2):
            _formation(fr, court, own, "receive", ann, g, t.frame - ann + 12, skip, busy)
        events = SAVE_EVENTS.get(t.link or 2, [8]) if t.formula == SAVE else ANNOUNCE_EVENTS.get(t.formula, [8])
        fr(ann).round_data.events.extend(events)
        _hit_data(fr(ann).round_data.action_data, t)
        _skills(fr(ann), t.casts, t.buffs)
        _extras(fr, t, ann)
        if t.formula in START_EVENTS or (t.formula == SAVE and (t.link or 2) == 2):
            fr(t.frame - lead).round_data.events.extend(START_EVENTS.get(t.formula, [9]))
        _anim(fr(t.frame - lead + 1), court, t.player, t.anim or anim, g + t.frame - lead, dur)
        _return(fr, court, t.player, t.frame - lead + dur + 1, g)
        if t.formula == SPIKE:  # the fake attack first: its jumper is busy when the others cover
            _feint(fr, court, t, lead, g, busy, prev_announce, {u.player for u in touches[i + 1:i + 4]})
            _formation(fr, court, own, "cover", t.frame, g, 14, skip, busy)
        _hit_data(fr(t.frame).round_data.hit_data, t)
        if t.formula == SET:
            fr(t.frame).round_data.events.append(14)
        prev, prev_announce = t, ann
    last = touches[-1]
    if last.result == 0 and last.formula == SERVE:  # service fault: ball out behind the far baseline
        s = side(1 - serve_team)
        land = (last.pos[0], 0.0, 10.5 * s)
        _ball_curve(fr(last.frame + 1), last.pos, land, g + last.frame, g + last.frame + 15, 1.3)
        end_frame = last.frame + 15 + END_AFTER
    elif last.result == 0 and last.touch_ball:  # e.g. block out: the ball flies off out of the court
        s = side(team_of(last.player))
        land = (5.5 if last.pos[0] >= 0 else -5.5, 0.0, 10.5 * s)
        _ball_curve(fr(last.frame + 1), last.pos, land, g + last.frame, g + last.frame + 24, 1.6)
        end_frame = last.frame + 24 + END_AFTER
    else:
        end_frame = last.frame + END_AFTER
    e = fr(end_frame)
    e.round_data.end = True
    e.round_data.events.append(21)
    e.game_data.id = 1
    _skills(e, *skill_end)
    out = [frames[i] for i in sorted(frames) if i <= end_frame]  # runs back after the end are dropped
    return out, end_frame
