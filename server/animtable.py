"""
Per-animation contact geometry from Garena's captured rallies -> data/anim_table.json.

For every touch the client plays the toucher's animation from where his run ended; the ball meets him
at a fixed offset from that spot. Measured from the captures (median per animation):
  dx, dz  ball contact point minus the player's position at animation start, in the player's frame
          (dz forward = towards the net, dx to the side; mirrored for the away team)
  y       contact height
  lead    frames from animation start to the hit
  dur     animation length in frames
rallygen uses it to stand the player where the animation brings his hands to the ball.
"""
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

import livematch as L

P = L.P
OUT = Path(__file__).parent / "data" / "anim_table.json"


def _local(dx, dz, fx, fz):
    """World offset -> the animation's own frame (right, forward) for facing (fx, fz)."""
    return dx * fz - dz * fx, dx * fx + dz * fz


def _world(r, f, fx, fz):
    return r * fz + f * fx, -r * fx + f * fz


def build():
    """All offsets are exact in the animation's own frame (zero spread in the captures): the client
    moves the player along the facing the server sends. They are measured there and stored turned to
    the animation's usual facing (fx, fz, team frame: +z = towards the net)."""
    contact, ends, facing = {}, {}, {}
    for r in L.library():
        anim, pend = {}, {}
        frames = r.frames
        for i, f in enumerate(frames):
            for p in f.player_data:
                cd = p.curve_data
                sgn = 1 if p.player_id < 13 else -1
                if p.player_id in pend and cd.curve_type == P.MOVE_CURVE:  # his next run starts where it ended
                    name, x, z, fx, fz, dur = pend.pop(p.player_id)
                    bz = cd.move_curve_data.bezier
                    ends.setdefault(name, []).append(_local(bz.p0.x - x, bz.p0.y - z, fx, fz) + (dur,))
                if cd.HasField("anim_curve_data") and cd.anim_curve_data.anim_name:
                    name = cd.anim_curve_data.anim_name
                    fx, fz = p.forward.x, p.forward.z
                    if not (fx or fz):
                        fx, fz = 0.0, float(sgn)
                    anim[p.player_id] = (cd.start_frame, cd.end_frame, p.position.x, p.position.z, fx, fz)
                    pend[p.player_id] = (name, p.position.x, p.position.z, fx, fz, cd.end_frame - cd.start_frame)
                    facing.setdefault(name, []).append((round(fx * sgn, 3), round(fz * sgn, 3)))
            if not (f.HasField("round_data") and f.round_data.HasField("hit_data")):
                continue
            h = f.round_data.hit_data
            a = anim.get(h.hit_player)
            if not a or not h.touch_ball:
                continue
            nxt = next((g for g in frames[i:i + 3] if g.HasField("ball_data")
                        and g.ball_data.curve_data.start_frame), None)
            lead = nxt.ball_data.curve_data.start_frame - a[0] if nxt else None
            contact.setdefault(h.hit_anim, []).append(
                _local(h.hit_ball_pos.x - a[2], h.hit_ball_pos.z - a[3], a[4], a[5])
                + (h.hit_ball_pos.y, lead, a[1] - a[0]))
    out = {}
    for name in set(contact) | set(ends):
        fx, fz = max(set(facing[name]), key=facing[name].count)  # the usual facing
        e = {"fx": fx, "fz": fz, "n": len(contact.get(name, [])), "ne": len(ends.get(name, []))}
        v = contact.get(name)
        if v:
            med = lambda k: st.median([x[k] for x in v if x[k] is not None]) if any(
                x[k] is not None for x in v) else None
            dx, dz = _world(med(0), med(1), fx, fz)
            e.update(dx=round(dx, 3), dz=round(dz, 3), y=round(med(2), 3), lead=med(3), dur=med(4))
        w = ends.get(name)
        if w:
            ex, ez = _world(st.median(x[0] for x in w), st.median(x[1] for x in w), fx, fz)
            e.update(ex=round(ex, 3), ez=round(ez, 3))
            e.setdefault("dur", st.median(x[2] for x in w))
        out[name] = e
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


if __name__ == "__main__":
    t = build()
    for k, v in sorted(t.items(), key=lambda x: -x[1]["n"])[:25]:
        print(k, v)
