"""
Bug hunt on whole matches: plays matches with random user inputs (hand serve, ultimates, morale skill,
substitutions, every libero preset) and checks the rules after every rally.

  court     six players per team on court, nobody twice, only valid ids
  touch     every touch by a player on court of the right team; libero never serves / spikes / blocks;
            blocks by front-row players; nobody twice in a row; at most 3 touches per side
  score     one point per rally, to the team the end frame names; the winner serves next
  rotation  a team that wins the serve rotates by one; the server is its slot-1 player
  libero    only in the back row, only for the preset job, never when all presets are off
  frames    frame indexes rising, one end frame, no player command after it
"""
import collections
import random
import sys

import engine
import livematch as L
import matches
import rallygen

P = matches.P
HOST, GUEST = 0, 1
FRONT = set(rallygen.FRONT)


def play(seed: int, out: collections.Counter, notes: list):
    random.seed(seed)
    c = matches._captured('0x0302', P.PVPCreateS2C)
    c.info.bo_score = random.choice((5, 15, 25))
    preset = random.choice((0, 1, 2, 3, 4))
    c.info.home.formation.libero_replace_type = preset
    m = L.LiveMatch(c.info, 0.5)
    m.auto_skill = random.random() < 0.3
    bench = [p for p in m.valid if p < 13 and p not in m.stance[HOST] and p != m.libero[HOST]]

    def bad(what, detail=""):
        out[what] += 1
        if len(notes) < 40:
            notes.append(f"seed {seed} rally {m.rally_no}: {what} {detail}")

    for _ in range(80):
        if m.over:
            break
        before_score, before_serve = list(m.score), m.serve
        before_stance = {k: list(v) for k, v in m.stance.items()}
        if bench and random.random() < 0.15:  # a substitution for the next rally
            on, off = random.choice(bench), random.choice(m.stance[HOST])
            req = P.PVPFormationChangeC2S()
            req.subs.on_stage.append(on)
            req.subs.off_stage.append(off)
            rep = P.PVPFormationChangeS2C.FromString(m.formation_change(req.SerializeToString()))
            if rep.subs.on_stage:
                bench.remove(on)
                bench.append(off)
                before_stance[HOST] = [on if p == off else p for p in before_stance[HOST]]
        frames = m.next_rally()
        for _ in range(random.choice((0, 0, 1, 2, 3))):  # user inputs re-simulate the rally
            kind = random.choice((0x0307, 0x030A, 0x030B))
            if kind == 0x0307:
                body = P.PVPHandleServeC2S(result=random.choice((1, 2, 3))).SerializeToString()
            elif kind == 0x030A:
                if not m.windows:
                    continue
                idx, frame = random.choice(m.windows)
                req = P.PVPUltimateSkillC2S()
                req.base.frame_index = frame + random.choice((-1, 0, 1))
                t = next((f for f in map(P.FrameData.FromString, frames) if f.HasField("skill_data")
                          and f.skill_data.ultimate_skills), None)
                if t:
                    u = random.choice(list(t.skill_data.ultimate_skills))
                    req.values.add(player_id=u.player_id, skill_id=u.skill_id)
                body = req.SerializeToString()
            else:
                body = P.PVPMoraleSkillC2S(hit_index=random.randint(0, 6)).SerializeToString()
            frames = m.apply_input(kind, body)
        fr = [P.FrameData.FromString(x) for x in frames]

        idx = [f.frame_index for f in fr]
        if idx != sorted(set(idx)):
            bad("frames: indexes not rising")
        ends = [f for f in fr if f.HasField("round_data") and f.round_data.end]
        if len(ends) != 1 or ends[0] is not fr[-1]:
            bad("frames: end frame missing or not last")
        setup = next((f for f in fr if f.frame_index == 2), None)
        court = {p.player_id for p in setup.player_data if not p.clear_curve} if setup else set()
        for team in (HOST, GUEST):
            mine = [p for p in court if (p >= 13) == bool(team)]
            if len(mine) != 6:
                bad("court: not six players", f"team {team} has {sorted(mine)}")
        if not court <= m.valid:
            bad("court: unknown player id")
        # who is on court per team, in slot order (libero in for his player)
        eff = {}
        for team in (HOST, GUEST):
            base = before_stance[team]
            lib, cur = m.libero[team], m.lib_for[team]
            eff[team] = [lib if (cur is not None and p == cur) else p for p in base]
            if set(eff[team]) != {p for p in court if (p >= 13) == bool(team)}:
                bad("court: setup frame differs from the stance", f"team {team} {eff[team]} vs {sorted(court)}")
            if lib in eff[team]:
                slot = eff[team].index(lib)
                if slot in FRONT:
                    bad("libero: in the front row")
                job = m.lib_job[team]
                if job is None:
                    bad("libero: in although all presets are off")
                elif m.jobs.get(cur) != job:
                    bad("libero: in for another job than the preset")
        hits = [f.round_data.hit_data for f in fr if f.HasField("round_data") and f.round_data.HasField("hit_data")]
        side_n, side_team, last = 0, None, None
        for h in hits:
            team = GUEST if h.hit_player >= 13 else HOST
            if h.hit_player not in court:
                bad("touch: player not on court", str(h.hit_player))
                continue
            slot = eff[team].index(h.hit_player) if h.hit_player in eff[team] else -1
            is_lib = h.hit_player == m.libero[team]
            if h.formula_type == engine.SERVE:
                if team != before_serve or slot != 0:
                    bad("rotation: server is not slot 1 of the serving team", f"slot {slot}")
                if is_lib:
                    bad("libero: serves")
            if h.formula_type in (engine.SPIKE, engine.BLOCK):
                if is_lib:
                    bad("libero: spikes or blocks")
                elif slot not in FRONT and h.formula_type == engine.BLOCK:  # back-row spikes: Garena has them
                    bad("touch: back-row player blocks")
            if h.touch_ball:
                if last is not None and last.hit_player == h.hit_player and last.formula_type != engine.BLOCK:
                    bad("touch: same player twice in a row")
                if h.formula_type == engine.BLOCK:
                    side_n, side_team = 0, team
                else:
                    side_n = side_n + 1 if team == side_team else 1
                    side_team = team
                    if side_n > 3:
                        bad("touch: four touches on one side")
                last = h
        # score, serve, rotation
        gd = fr[-1].game_data
        diff = [m.score[0] - before_score[0], m.score[1] - before_score[1]]
        if sorted(diff) != [0, 1]:
            bad("score: not exactly one point", str(diff))
        else:
            point = diff.index(1)
            if gd.score.score_team != point or [gd.score.host_score, gd.score.guest_score] != m.score:
                bad("score: end frame differs from the match score")
            if m.serve != point:
                bad("score: the winner does not serve next")
            for team in (HOST, GUEST):
                want = before_stance[team]
                if team == point and point != before_serve:
                    want = want[1:] + want[:1]
                if m.stance[team] != want:
                    bad("rotation: wrong stance after the rally", f"team {team}")
        if m.over != (max(m.score) >= m.target):
            bad("score: match end flag wrong")
    return m


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    out, notes, crashes = collections.Counter(), [], 0
    rallies = 0
    for seed in range(n):
        try:
            rallies += play(seed, out, notes).rally_no
        except Exception as e:  # a crash is a bug too
            import traceback
            crashes += 1
            if crashes <= 3:
                traceback.print_exc()
            out["crash: " + type(e).__name__] += 1
    print(f"{n} matches, {rallies} rallies")
    for k, v in sorted(out.items()):
        print(f"{v:6d}  {k}")
    for line in notes[:25]:
        print("  ", line)
    if not out:
        print("no rule broken")
