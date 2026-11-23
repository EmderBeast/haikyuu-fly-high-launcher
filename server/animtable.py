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


def measure():
    rows = defaultdict(list)
    for r in L.library():
        anim = {}
        for f in r.frames:
            for p in f.player_data:
                cd = p.curve_data
                if cd.HasField("anim_curve_data") and cd.anim_curve_data.anim_name:
                    anim[p.player_id] = (cd.start_frame, cd.end_frame, p.position.x, p.position.z)
            if not (f.HasField("round_data") and f.round_data.HasField("hit_data")):
                continue
            h = f.round_data.hit_data
            a = anim.get(h.hit_player)
            if not a or not h.touch_ball:
                continue
            s = 1 if h.hit_player < 13 else -1
            hit_global = a[0] + (f.frame_index - 0)  # placeholder, replaced below
            rows[h.hit_anim].append(((h.hit_ball_pos.x - a[2]) * s, (h.hit_ball_pos.z - a[3]) * s,
                                     h.hit_ball_pos.y, a[0], a[1], h.hit_frame, f.frame_index))
    return rows


def build():
    # global hit frame = anim start + (local hit frame - local frame where the anim was announced) is not
    # directly known; the ball curve of the next frame starts at the global hit frame, so use that
    table = {}
    for r in L.library():
        anim = {}
        frames = r.frames
        for i, f in enumerate(frames):
            for p in f.player_data:
                cd = p.curve_data
                if cd.HasField("anim_curve_data") and cd.anim_curve_data.anim_name:
                    anim[p.player_id] = (cd.start_frame, cd.end_frame, p.position.x, p.position.z,
                                         p.forward.x, p.forward.z)
            if not (f.HasField("round_data") and f.round_data.HasField("hit_data")):
                continue
            h = f.round_data.hit_data
            a = anim.get(h.hit_player)
            if not a or not h.touch_ball:
                continue
            nxt = next((g for g in frames[i:i + 3] if g.HasField("ball_data")
                        and g.ball_data.curve_data.start_frame), None)
            lead = nxt.ball_data.curve_data.start_frame - a[0] if nxt else None
            s = 1 if h.hit_player < 13 else -1
            table.setdefault(h.hit_anim, []).append(
                ((h.hit_ball_pos.x - a[2]) * s, (h.hit_ball_pos.z - a[3]) * s, h.hit_ball_pos.y,
                 lead, a[1] - a[0], a[4] * s, a[5] * s))
    out = {}
    for name, v in table.items():
        med = lambda k: st.median([x[k] for x in v if x[k] is not None]) if any(x[k] is not None for x in v) else None
        out[name] = {"dx": round(med(0), 3), "dz": round(med(1), 3), "y": round(med(2), 3),
                     "lead": med(3), "dur": med(4), "fx": round(med(5), 3), "fz": round(med(6), 3), "n": len(v)}
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


if __name__ == "__main__":
    t = build()
    for k, v in sorted(t.items(), key=lambda x: -x[1]["n"])[:25]:
        print(k, v)
