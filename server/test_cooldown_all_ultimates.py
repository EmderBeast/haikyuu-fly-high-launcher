"""Offline test: a cooldown buff with selector 6 (ModifyTarget [mode, 6]) shortens every Ultimate of the
player, not only Block Ultimates. Garena's own cooldowns (record_cd_info_skills): under 5702's
57021131 ([1, 6] -4) Hoshiumi's spike Ultimate 57014111 started at 8 - 4. Skill texts 5505203 / 6002303
("Ultimate cooldowns ... decrease").
  python test_cooldown_all_ultimates.py
"""
import engine
import matches
import skills

P = matches.P


def main():
    if not skills.AVAILABLE or 57021131 not in skills.BUFFS:
        print("skill tables missing: nothing to test")
        return
    c = matches._captured('0x0302', P.PVPCreateS2C)
    b = skills.Battle(c.info, engine.stats_for(c.info))
    pid = min(b.cards)
    spike = next(s for i, s in sorted(skills.SKILLS.items()) if skills._num(s.get("Type")) == 6
                 and skills._stag(s) != 2 and skills._num(s.get("CDType")) == 6)
    b.skills[pid] = [(spike, 1)]
    base = b._cd_length(pid, spike)
    b.add_buff(("p", pid), 57021131, 1, pid)
    cut = b._cd_length(pid, spike)
    arg = skills._flat(skills.BUFFS[57021131]["Args"])[0] // 10000
    ok = cut == max(base + arg, 0)
    print(("ok   " if ok else "FAIL ") + f"non-Block Ultimate {spike['ID']}: cooldown {base} -> {cut} under 57021131 ({arg:+d})")
    skills.SEL6_ALL = False
    old = b._cd_length(pid, spike)
    skills.SEL6_ALL = True
    ok2 = old == base
    print(("ok   " if ok2 else "FAIL ") + f"switch off: {old} (Block Ultimates only)")
    raise SystemExit(0 if ok and ok2 else 1)


if __name__ == "__main__":
    main()
