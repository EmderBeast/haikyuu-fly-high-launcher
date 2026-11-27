"""Offline test of the bond levels of a line-up (bondlevels.py):
  - every captured room side with bonds (decoded/*match_msgs.json): our fetter_id_levels from its 13 cards =
    Garena's (as a set; Garena's order is not fixed). Away sides of bots carry none and are skipped.
  - bond 1153 (5101 + 5102): stars 6 / 6 -> level 5 rows 115305 + 115310, stars 6 / 3 -> level 2, one member
    -> none; a bench card counts; four players of one school -> school bond row
  python test_bondlevels.py
"""
import json
from pathlib import Path

import bondlevels as B
import gameconfig
import matches

P = matches.P
DECODED = Path(__file__).parent.parent / "decoded"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def body(m):
    b = bytes.fromhex(m["hex"])
    return b[1:] if b[:1] == b"\x00" else b


def captures():
    n, bad = 0, []
    for path in sorted(DECODED.glob("*match_msgs.json")):
        for m in json.loads(path.read_text()):
            if m["dir"] != "S->C" or m["msg_id"] != "0x0302":
                continue
            info = P.PVPCreateS2C.FromString(body(m)).info
            for side in (info.home, info.away):
                garena = list(side.formation.fetter_id_levels)
                if not garena and side is info.away:
                    continue
                ours = B.fetter_ids([(p.info.conf_id, p.info.star) for p in side.formation.players])
                n += 1
                if set(ours) != set(garena) or len(ours) != len(garena):
                    bad.append((path.name, sorted(garena), sorted(ours)))
    if not n:
        print("skip captures (no decoded/*match_msgs.json)")
        return
    check(f"captured rooms: fetter_id_levels from the cards = Garena's ({n} sides)", not bad, bad[:2])


def rules():
    lvl5 = B.fetter_ids([(5101, 6), (5102, 6), (1105, 2)])
    check("bond 1153 at stars 6/6: level 5 rows for both players", {115305, 115310} <= set(lvl5), lvl5)
    lvl2 = [i for i in B.fetter_ids([(5101, 6), (5102, 3)]) if i // 100 == 1153]
    check("stars 6/3: level 2 (lowest star - 1)", lvl2 and all(B.ROWS[(1153, 2)].count(i) for i in lvl2), lvl2)
    check("one member only: no bond 1153", not [i for i in B.fetter_ids([(5101, 6)]) if i // 100 == 1153])
    school = next(b for b in B.BONDS if b["Type"] == 3 and b.get("School") == 11)
    kara = [c for c, r in sorted(gameconfig.PLAYERS.items()) if r.get("SchoolID") == 11][:4]
    rows = B.ROWS[(school["ID"], 1)]
    check("four players of one school: school bond", set(rows) <= set(B.fetter_ids([(c, 2) for c in kara])), rows)
    check("three are not enough", not set(rows) & set(B.fetter_ids([(c, 2) for c in kara[:3]])))


if not B.ROWS:
    print("skip: tables missing (gamedata)")
else:
    captures()
    rules()
    print(f"{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)
