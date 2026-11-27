"""Offline test: the coach's position buffs (coachbuff.CoachBuffId) reach the players of that position,
also when the row names several skills (80 of 330 rows, e.g. 4071 [6113008, 6113018]).
  python test_coachbuff.py
"""
import engine
import matches
import skills

P = matches.P


def main():
    if not skills.AVAILABLE:
        print("skill tables missing: nothing to test")
        return
    multi = [r for r in skills.COACHBUFF.values() if len(skills._flat(r.get("CoachBuffId"))) > 1
             and all(i in skills.SKILLS for i in skills._flat(r.get("CoachBuffId")))]
    single = [r for r in skills.COACHBUFF.values() if len(skills._flat(r.get("CoachBuffId"))) == 1
              and skills._flat(r.get("CoachBuffId"))[0] in skills.SKILLS]
    bad = []
    for row in multi[:20] + single[:5]:
        c = matches._captured('0x0302', P.PVPCreateS2C)
        t = c.info.home
        del t.skill_extras[:]
        for p in t.formation.players:
            del p.skill_extras[:]
        t.formation.coach_card.ClearField("job_buffs")
        jobs = skills.Battle(c.info, engine.stats_for(c.info)).jobs
        job = next(j for p, j in sorted(jobs.items()) if p < 13 and j)
        jb = t.formation.coach_card.job_buffs.add()
        jb.buff_id, jb.job = row["ID"], job
        b = skills.Battle(c.info, engine.stats_for(c.info))
        want = skills._flat(row["CoachBuffId"])
        for pid in [p for p in b.cards if p < 13 and b.jobs[p] == job]:
            have = [s["ID"] for s, _ in b.skills[pid]]
            if any(have.count(i) < 1 for i in want):
                bad.append((row["ID"], pid, want))
    print(f"{len(multi)} rows with several skills, {len(single)} with one; checked {min(len(multi), 20) + min(len(single), 5)}")
    print("ok   every listed skill reaches the players of the position" if not bad else f"FAIL {bad[:5]}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
