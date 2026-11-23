"""Offline check of generated rallies: every position a player is sent to or carried to by an
animation must be on his own side; counts runs and feints per rally."""
import collections

import livematch as L
import matches
import rallygen

P = matches.P
c = matches._captured('0x0302', P.PVPCreateS2C)
c.info.bo_score = 60
m = L.LiveMatch(c.info, 0.5)
bad = collections.Counter()
feints = nm = R = 0
for k in range(90):
    fr = m.next_rally()
    assert fr == m._play()
    R += 1
    for x in fr:
        f = P.FrameData.FromString(x)
        for p in f.player_data:
            if p.clear_curve:
                continue
            home = p.player_id < 13
            face = 1 if home else -1
            cd = p.curve_data
            name = cd.anim_curve_data.anim_name
            pts = []
            if cd.curve_type == P.MOVE_CURVE:
                nm += 1
                b = cd.move_curve_data.bezier
                pts += [("run start", b.p0.y), ("run end", b.p1.y)]
            else:
                pts.append(("anim start" if name else "standby", p.position.z))
            if name:
                ex, ez = rallygen.end_offset(name)
                pts.append(("anim end " + name, p.position.z + ez * face))
                if name.startswith('feign'):
                    feints += 1
            for what, z in pts:
                if (home and z > 0.05) or (not home and z < -0.05):
                    bad[what] += 1
    if m.over:
        break
print('rallies', R, 'runs/rally', round(nm / R, 1), 'feints', feints, 'score', m.score)
print('wrong side:', dict(bad) or 'none')
