"""
Bug hunt on rallies: runs the same checks on our generated rallies and on Garena's captured ones, so a
check that also fires on Garena's is not a bug.

  net       a ball flight that crosses the net lower than the net (2.43 m) or lands outside
  teleport  a run / animation / standby that starts away from where the player was
  overlap   a new run or animation for a player while his last animation is still playing
  late      an animation that starts before the player's run to it is over
  side      a player sent to / carried to the other side of the net
  chain     touch order broken (same player twice in a row, 4 touches on a side, wrong side's touch)
"""
import collections
import math
import sys

import livematch as L
import matches
import rallygen

P = matches.P
NET = 2.43


def check(frames, g0, out, anim_end_known=True):
    pos, busy, moving, runs = {}, {}, {}, {}
    hits = []
    for f in frames:
        gi = g0 + f.frame_index
        for p in f.player_data:
            if p.clear_curve:
                continue
            pid = p.player_id
            home = pid < 13
            face = 1 if home else -1
            cd = p.curve_data
            name = cd.anim_curve_data.anim_name
            if cd.curve_type == P.MOVE_CURVE:
                b = cd.move_curve_data.bezier
                start, end = (b.p0.x, b.p0.y), (b.p1.x, b.p1.y)
                was = pos.get(pid)
                r = runs.get(pid)
                if r and r[1] > cd.start_frame:  # cut short: he is somewhere on the way
                    u = (cd.start_frame - r[0]) / max(r[1] - r[0], 1)
                    lo, hi = max(u - 0.35, 0), min(u + 0.35, 1)
                    was = min(((r[2][0] + (r[3][0] - r[2][0]) * (lo + (hi - lo) * k / 10),
                                r[2][1] + (r[3][1] - r[2][1]) * (lo + (hi - lo) * k / 10)) for k in range(11)),
                              key=lambda q: math.dist(q, start))
                if was and math.dist(was, start) > 0.8:
                    out["teleport run %.0fm" % min(math.dist(was, start), 9)] += 1
                runs[pid] = (cd.start_frame, cd.end_frame, start, end)
                if busy.get(pid, 0) > cd.start_frame + 1:
                    out["overlap run during animation"] += 1
                pos[pid] = end
                moving[pid] = cd.end_frame
                if (home and end[1] > 0.05) or (not home and end[1] < -0.05):
                    out["side run"] += 1
            else:
                here = (p.position.x, p.position.z)
                if not p.HasField("position"):
                    continue
                if name:
                    if pid in pos and math.dist(pos[pid], here) > 0.6:
                        out["teleport animation"] += 1
                    if moving.get(pid, 0) > cd.start_frame + 2:
                        out["late: animation starts %d+ frames before the run ends" % min(
                            5 * ((moving[pid] - cd.start_frame) // 5), 15)] += 1
                    if busy.get(pid, 0) > cd.start_frame + 1:
                        out["overlap animation during animation"] += 1
                    ex, ez = rallygen.end_offset(name)
                    fx, fz = p.forward.x, p.forward.z
                    cx, cz = rallygen.anim_forward(name)
                    # table offsets are for the usual facing; turn them to the facing actually sent
                    ang = math.atan2(fx, fz) - math.atan2(cx * face, cz * face)
                    wx = ex * face * math.cos(ang) + ez * face * math.sin(ang)
                    wz = -ex * face * math.sin(ang) + ez * face * math.cos(ang)
                    pos[pid] = (here[0] + wx, here[1] + wz)
                    busy[pid] = cd.end_frame
                    if (home and pos[pid][1] > 0.05) or (not home and pos[pid][1] < -0.05):
                        out["side animation end"] += 1
                else:
                    if pid in pos and math.dist(pos[pid], here) > 0.6:
                        out["teleport standby"] += 1
                    pos[pid] = here
        if f.HasField("ball_data") and f.ball_data.curve_data.curve_type == P.FLY_CURVE:
            fc = f.ball_data.curve_data.fly_curve_data
            m = fc.plane_to_world
            x0, z0 = m.c3.x, m.c3.z
            ux, uz = m.c0.x, m.c0.z
            d = fc.end_pos2_d.x
            x1, z1 = x0 + ux * d, z0 + uz * d
            if z0 * z1 < 0 and abs(uz) > 1e-6:  # crosses the net plane
                dn = -z0 / uz
                h = fc.a * dn * dn + fc.b * dn + fc.c
                if h < NET:
                    out["net: ball crosses below the net"] += 1
            if fc.end_pos2_d.y <= 0.01 and (abs(x1) > 4.6 or abs(z1) > 9.1):
                out["ball lands out"] += 1
        if f.HasField("round_data") and f.round_data.HasField("hit_data"):
            hits.append(f.round_data.hit_data)
    side_touches, last_team = 0, None
    for i, h in enumerate(hits):
        team = 0 if h.hit_player < 13 else 1
        if i and hits[i - 1].hit_player == h.hit_player and hits[i - 1].touch_ball and h.touch_ball \
                and hits[i - 1].formula_type != 6:
            out["chain: same player twice in a row"] += 1
        if h.formula_type == 6 or not h.touch_ball:
            last_team = team if h.formula_type == 6 else last_team
            side_touches = 0 if h.formula_type == 6 else side_touches
            continue
        side_touches = side_touches + 1 if team == last_team else 1
        last_team = team
        if side_touches > 3:
            out["chain: 4 touches on one side"] += 1
        if h.touch_ball and ((team == 0 and h.hit_ball_pos.z > 0.6) or (team == 1 and h.hit_ball_pos.z < -0.6)):
            out["chain: touch on the other side"] += 1
    return len(hits)


def ours(n=150):
    c = matches._captured('0x0302', P.PVPCreateS2C)
    c.info.bo_score = 9999
    m = L.LiveMatch(c.info, 0.5)
    out = collections.Counter()
    touches = 0
    for _ in range(n):
        g0 = m.global_frame
        fr = [P.FrameData.FromString(x) for x in m.next_rally()]
        touches += check(fr, g0, out)
    return out, n, touches


def garena():
    out = collections.Counter()
    touches = 0
    for r in L.LIBRARY:
        touches += check(r.frames, L.LiveMatch._captured_offset(r), out)
    return out, len(L.LIBRARY), touches


if __name__ == "__main__":
    a, na, ta = ours(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
    b, nb, tb = garena()
    print(f"ours: {na} rallies, {ta} touches | garena: {nb} rallies, {tb} touches")
    print(f"{'check':58s} ours/rally  garena/rally")
    for k in sorted(set(a) | set(b)):
        print(f"{k:58s} {a[k] / na:9.2f}  {b[k] / nb:11.2f}")
