"""Offline test: a cooldown reset buff (Type 5, mode 0, -99) the player already holds when a skill enters
its cooldown resets it then (5502 "when he uses [DIVINE REFLEXES] ... resets its cooldown": the reset
buff 55023141 comes with the touch, before the cooldown starts). Garena's cooldowns in the frames
(record_cd_info_skills) showed the reset on all 41 captured casts of 55024111.
  python test_cooldown_reset.py
"""
import engine
import matches
import skills

P = matches.P


def main():
    if not skills.AVAILABLE or 55023141 not in skills.BUFFS or 55024111 not in skills.SKILLS:
        print("skill tables missing: nothing to test")
        return
    c = matches._captured('0x0302', P.PVPCreateS2C)
    b = skills.Battle(c.info, engine.stats_for(c.info))
    pid = min(b.cards)
    ult = skills.SKILLS[55024111]
    b.skills[pid] = [(ult, 1)]
    b.energy[pid] = skills.ENERGY_MAX
    ctx = skills.Ctx("touch", pid)
    b.add_buff(("p", pid), 55023141, 1, pid)
    b._run(ult, pid, 1, ctx, check=False)
    ok = (pid, 55024111) not in b.cd
    print(("ok   " if ok else "FAIL ") + "reset buff held before the cast: no cooldown after it")
    b.cd.pop((pid, 55024111), None)
    b.remove_buff(("p", pid), 55023141)
    b._run(ult, pid, 1, ctx, check=False)
    ok2 = (pid, 55024111) in b.cd
    print(("ok   " if ok2 else "FAIL ") + "without it: the cooldown starts")
    raise SystemExit(0 if ok and ok2 else 1)


if __name__ == "__main__":
    main()
