"""Offline test of the active bonds after substitutions (skills.active_bonds, LiveMatch.formation_change,
Battle bond skills):
  - every captured match start (rally 1, team_data.trammel_ids of the user's team) and every captured
    0x0309 reply (PVPFormationChangeS2C.trammel_ids): line-up and substitutions replayed, our list must
    equal Garena's (same ids, same order)
  - a bond member on the bench does not count at the start, once subbed in it does (its skills run),
    subbed out again its skills stop; substitutions of the server's side (in the frames) change nothing
Needs the decoded captures (decoded/*match_msgs.json); without them only the second part runs.
  python test_bond_subs.py
"""
import json
from pathlib import Path

import engine
import livematch
import matches
import skills

P = matches.P
DECODED = Path(__file__).parent.parent / "decoded"
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def body(m):
    b = bytes.fromhex(m["hex"])
    return b[1:] if b[:1] == b"\x00" else b


def frames_of(m):
    cls = livematch.CLASSES.get(m["msg_id"])
    if cls is None:
        return []
    try:
        return [P.FrameData.FromString(r) for r in cls.FromString(body(m)).frames]
    except Exception:
        return []


def replay_captures():
    starts = subs = 0
    bad = []
    for path in sorted(DECODED.glob("*match_msgs.json")):
        live, start_seen = None, False
        for m in json.loads(path.read_text()):
            if m["dir"] != "S->C":
                continue
            if m["msg_id"] == "0x0302":
                live = livematch.LiveMatch(P.PVPCreateS2C.FromString(body(m)).info, 0.5)
                start_seen = False
                continue
            if live is None:
                continue
            if not start_seen:
                for f in frames_of(m):
                    for t in f.team_data:
                        if t.have_trammel and t.team_id == 0 and f.game_id == 1 and f.round_id == 1:
                            start_seen = True
                            starts += 1
                            ours = live.bond_ids(0)
                            if ours != list(t.trammel_ids):
                                bad.append((path.name, "start", list(t.trammel_ids), ours))
            if m["msg_id"] == "0x0309":
                garena = P.PVPFormationChangeS2C.FromString(body(m))
                req = P.PVPFormationChangeC2S()
                req.subs.on_stage.extend(garena.subs.on_stage)
                req.subs.off_stage.extend(garena.subs.off_stage)
                rep = P.PVPFormationChangeS2C.FromString(live.formation_change(req.SerializeToString()))
                subs += 1
                if list(rep.subs.on_stage) != list(garena.subs.on_stage):
                    bad.append((path.name, "subs refused", list(garena.subs.on_stage), list(rep.subs.on_stage)))
                if list(rep.trammel_ids) != list(garena.trammel_ids):
                    bad.append((path.name, "0x0309", list(garena.trammel_ids), list(rep.trammel_ids)))
                live.apply_subs()  # Garena's next rally is played with them
    if not starts:
        print("skip captures (no decoded/*match_msgs.json)")
        return
    check(f"captured match starts and 0x0309 replies: our trammel_ids = Garena's ({starts} starts, {subs} subs)",
          not bad, "; ".join(map(str, bad[:5])))
    check("captures contain substitutions", subs >= 20, subs)


def bench_member():
    """Bond 1153 (5101 + 5102, skills of 5101) with 5101 in the libero slot and 5102 on the bench
    (line-up of hfa_1811)."""
    lineup = [5701, 1105, 5702, 5104, 5802, 5301, 5101, 5102, 5302, 5501, 2701, 5402, 5401]
    info = P.PVPRoomInfo()
    info.home.formation.fetter_id_levels.extend([121305, 121315, 115310, 115305])
    for cid in lineup:
        info.home.formation.players.add().info.conf_id = cid
    for cid in [1527, 1506, 1507, 1503, 1512, 1501, 1513]:
        info.away.formation.players.add().info.conf_id = cid
    b = skills.Battle(info, engine.stats_for(info))
    bond = {s for s in skills._list(skills.SKILLLIST[115305]["Skillid"])}

    def has_bond():
        return bond & {sk["ID"] for sk, _ in b.skills[6]}

    away = set(range(13, 19))
    check("bench member does not count at the start", b.bonds[0] == [121305, 121315] and not has_bond(),
          (b.bonds[0], has_bond()))
    b.rally_start({0, 1, 2, 3, 4, 5} | away)  # first rally, nobody changed
    check("still inactive after a rally without substitution", not has_bond())
    b.substitute(7, 5)  # the user's substitution (0x0309): 5102 comes in for 5301
    b.rally_start({0, 1, 2, 3, 4, 7} | away)
    check("subbed in: bond active, its skills belong to 5101",
          b.bonds[0] == [121305, 121315, 115310, 115305] and has_bond() == bond, (b.bonds[0], has_bond()))
    b.rally_start({0, 1, 2, 3, 4, 5} | away)  # and goes out again (seen on the court only, as in replays)
    check("subbed out again: bond skills stop", b.bonds[0] == [121305, 121315] and not has_bond(),
          (b.bonds[0], has_bond()))
    b.rally_start({0, 1, 2, 3, 4, 7} | away)  # in again; 5101 (libero slot) off court
    check("the libero slot counts even when the libero is off court", has_bond() == bond)
    b.rally_start({0, 1, 2, 3, 4, 5} | away, subs=[(5, 7)])  # a substitution sent in the frames
    check("substitutions sent in the frames (server side) leave the bonds as they are", has_bond() == bond)

    live = livematch.LiveMatch(info, 0.5)
    rep = P.PVPFormationChangeS2C.FromString(live.formation_change(
        P.PVPFormationChangeC2S(subs=P.Substitution(on_stage=[7], off_stage=[5])).SerializeToString()))
    check("0x0309 reply lists the bond that the substitution activates",
          list(rep.trammel_ids) == [121305, 121315, 115310, 115305], list(rep.trammel_ids))
    check("school bond (id < 100) counted by its school's players",
          skills.active_bonds([11], [1110, 1111, 1109, 1104, 2704, 1702, 5501]) == [11]
          and skills.active_bonds([11], [5401, 1111, 1109, 1104, 2704, 1702, 5501]) == [], "")


if not skills.AVAILABLE:
    print("skip: skill tables missing (gamedata)")
else:
    replay_captures()
    bench_member()
    print(f"{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)
