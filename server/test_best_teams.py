"""Offline test of the Admin Mode action that loads the Fly High Hub's four best teams (bestteams.py,
queue {"best_teams": {"edition", "focus"}}, command /bestteams): every player of the four teams is owned at
level 80 with the Hub's skills, memory and six potentials (the focus team's build where they differ), one coach
per team with the Hub's Positional Advantages, line-ups 0-3 with court 1-6, libero, bench, libero replacement,
Lineup Specialty, cheers and coach; /bestteams off puts the old line-ups back.
  python test_best_teams.py
"""
import json
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-bestteams-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402,F401
import admin  # noqa: E402
import bestteams  # noqa: E402
import coach  # noqa: E402
import gameconfig  # noqa: E402
import matches  # noqa: E402
import scanimport  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def main():
    data = bestteams.load()
    if not gameconfig.AVAILABLE or not data:
        print("tables or data/best_teams.json missing: nothing to test")
        return
    import os
    ed = os.environ.get("BT_EDITION") or "jp" if all(p["id"] in gameconfig.PLAYERS for t in data["teams"]["jp"] for p in bestteams._team_players(t)) \
        else "global"
    teams = data["teams"][ed]
    check("parse /bestteams", bestteams.parse_args(["JP", "pow"]) == {"edition": "jp", "focus": "power"}
          and bestteams.parse_args(["x"]) is None and bestteams.parse_args(["off"]) == {"off": True})
    acc = accounts.Account(9995)
    old_team = acc.team_info()
    report = bestteams.apply(acc, {"edition": ed, "focus": "power"})
    print("   " + "\n   ".join(report))
    inst = bestteams._best_inst(acc)
    info = P.TeamInfoS2C.FromString(acc.team_info())
    forms = {f.lineup: f for f in info.formations}
    check("four line-ups", all(i in forms for i in range(4)))
    names = {n.lineup: n.name for n in info.formation_name_sli}
    check("line-up names", names.get(1, "").endswith("vs Power"), names)
    st = coach._store(acc.gid)
    for i, t in enumerate(sorted(teams, key=lambda t: bestteams.OPPS.index(t["opp"]))):
        f = forms[i]
        want = [inst.get(p["id"]) for p in t["court"]] + [inst.get(t["libero"]["id"])] \
            + [inst.get(p["id"]) for p in t["bench"]]
        check(f"{t['opp']}: court, libero, bench", list(f.inst_ids)[:len(want)] == want and 0 not in want)
        check(f"{t['opp']}: libero for {t['liberoFor']}", f.libero_replace_type == bestteams.LIBERO_FOR[t["liberoFor"]])
        check(f"{t['opp']}: specialty {t['genre']}", f.genre.value == t["genre"])
        check(f"{t['opp']}: cheers", list(f.player_supports) == [1, 6, 8], list(f.player_supports))
        raw = st["cards"].get(str(f.coach_card_inst_id))
        cc = P.CoachCard.FromString(bytes.fromhex(raw)) if raw else None
        want_conf = scanimport.coach_conf(bestteams._coach(t["coach"])["name"], t["coach"]["lv"])
        check(f"{t['opp']}: coach {bestteams._coach(t['coach'])['name']} Lv {t['coach']['level']} with the Hub's PAs",
              cc is not None and cc.conf_id == want_conf and cc.level == t["coach"]["level"]
              and len(cc.job_buffs) == len(t["coach"]["pas"]), cc and (cc.conf_id, cc.level, len(cc.job_buffs)))
    # the power team's builds: skills, memory, potentials of its court players
    pw = next(t for t in teams if t["opp"] == "power")
    for p in pw["court"][:3]:
        card = wire.parse(acc.get_card(inst[p["id"]]))
        specs = [s for s in admin._slots(acc.get_card(inst[p["id"]])) if s]
        check(f"{p['id']}: level 80, 6 potentials, memory", wire.get(card, 4, 0) == 80 and len(specs) == len(p["pots"])
              and (wire.get(card, 12, 0) != 0) == bool(p.get("memory")), (wire.get(card, 4, 0), len(specs)))
    out = bestteams.apply(acc, {"off": True})
    check("off: old line-ups back", acc.team_info() == old_team, out)
    print(f"\n{sum(checks)}/{len(checks)} ok")
    if not all(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
