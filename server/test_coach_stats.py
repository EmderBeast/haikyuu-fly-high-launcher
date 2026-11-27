"""Offline test of the coach's share in a match team (matches.coach_values / switch_coach). Client code
TeamView.TeamCalculator.AddCoachAttr: every coachlist.PropertyNumber value + coachlevel.Property of the
coach's level goes to each player; Garena's rooms carry coachlist.PropertyBuff as a team skill_extra and
the job_buffs' skills on the players of that job.
  python test_coach_stats.py
"""
import gameconfig
import matches

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def main():
    if not gameconfig.AVAILABLE:
        print("tables missing: nothing to test")
        return
    c = matches._captured('0x0302', P.PVPCreateS2C)
    team = c.info.home
    old = P.CoachCard()
    old.CopyFrom(team.formation.coach_card)
    ov = matches.coach_values(old)
    check("captured coach 12 level 15: 42 + 168 per basic stat", ov and set(ov.values()) == {2100000}, str(ov))
    check("its PropertyBuff is in the room's extras", matches.coach_skills(old)[0][0] in {e.skill_id for e in team.skill_extras})

    # another coach of another level: captured cards lose the old share and get the new one
    other = next(r for r in sorted(matches._table_cached("coachlist").values(), key=lambda r: r["ID"])
                 if r["ID"] != old.conf_id and matches._kv_pairs(r.get("PropertyNumber")))
    new = P.CoachCard(conf_id=other["ID"], level=5, inst_id=1)
    nv = matches.coach_values(new)
    before = {p.info.inst_id: [a.extrval for a in p.attr] for p in team.formation.players}
    types = [[a.type for a in p.attr] for p in team.formation.players]
    team.formation.coach_card.CopyFrom(new)
    matches.switch_coach(team, old, set(before))
    p0 = team.formation.players[0]
    want = [v + nv.get(t, 0) - ov.get(t, 0) for v, t in zip(before[p0.info.inst_id], types[0])]
    check("captured card: new coach's share instead of the old one's", [a.extrval for a in p0.attr] == want)
    extras = {e.skill_id for e in team.skill_extras}
    o_team, o_jobs = matches.coach_skills(old)
    n_team, _ = matches.coach_skills(new)
    check("team extras: old PropertyBuff out, new one in", not (set(o_team) - set(n_team)) & extras and set(n_team) <= extras,
          f"{sorted(extras)}")
    old_job = {s for ids in o_jobs.values() for s in ids}
    left = {e.skill_id for p in team.formation.players for e in p.skill_extras} & old_job
    check("players: the old coach's job skills are gone", not left, str(sorted(left)))

    # a card without captured stats gets the whole share
    team2 = P.PVPTeamInfo()
    team2.formation.players.add().attr.extend([P.PlayerCardAttr(type=t, val=100, extrval=0) for t in range(7)])
    team2.formation.coach_card.CopyFrom(new)
    matches.switch_coach(team2, None, set())
    got = [a.extrval for a in team2.formation.players[0].attr]
    check("own card: the coach's whole share", got == [nv.get(t, 0) for t in range(7)], str(got))

    print(f"\n{sum(checks)}/{len(checks)} ok")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
