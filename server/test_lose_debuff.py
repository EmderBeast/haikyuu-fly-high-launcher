"""Offline test of the ILoseDeBuff trigger (skills._debuff_lost): a player's "when he loses a debuff"
skills are checked once per stack of a debuff (buff table BuffType2 1) that is removed or runs out.
Not evidenced by a capture (no recorded match has such a skill); read from the skill texts.
Uses the JP player 5604 when his rows are in the tables, else the Garena skill 53055221.
  python test_lose_debuff.py
"""
import engine
import matches
import skills

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def battle(skill_id):
    c = matches._captured('0x0302', P.PVPCreateS2C)
    b = skills.Battle(c.info, engine.stats_for(c.info))
    pid = min(b.cards)
    b.skills[pid] = [(skills.SKILLS[skill_id], 1)]
    return b, pid


def main():
    if not skills.AVAILABLE:
        print("skill tables missing: nothing to test")
        return
    skill_id = 56043111 if 56043111 in skills.SKILLS else 53055221
    gain = skills._num(skills.SKILLS[skill_id]["SkillEffect"][0]["V1"])
    other_clock = skills._num(skills.BUFFS[gain].get("Time"), 0)  # the debuff must not run out with the gain
    debuff = next(i for i, r in sorted(skills.BUFFS.items())
                  if skills._num(r.get("BuffType2")) == 1 and skills._num(r.get("OverlayType")) == 1
                  and skills._num(r.get("Time"), 0) != other_clock
                  and skills._num(r.get("OverlayLimit"), 0) >= 3 and skills._num(r.get("Time"), 0)
                  and skills._num(r.get("MountType"), 1) == 1 and skills._num(r.get("Type")) == 2
                  and skills._flat(r.get("ModifyTarget")) != [skills.STAMINA])
    unit = skills._num(skills.BUFFS[debuff]["Time"])
    plain = next(i for i, r in sorted(skills.BUFFS.items())
                 if skills._num(r.get("BuffType2"), 0) != 1 and skills._num(r.get("Type")) == 2
                 and skills._num(r.get("MountType"), 1) == 1
                 and skills._flat(r.get("ModifyTarget")) != [skills.STAMINA] and skills._tag(r) is None)
    print(f"skill {skill_id} gives {gain}; debuff {debuff} (time unit {unit}); plain buff {plain}")

    b, pid = battle(skill_id)
    me = ("p", pid)
    check("event of the skill", b.event_of(skills.SKILLS[skill_id]) == "losedebuff")
    b.add_buff(me, debuff, 3, pid)
    check("getting a debuff gives nothing", b.stacks(me, gain) == 0)
    b.remove_buff(me, debuff, 1)
    check("one stack removed: one stack gained", b.stacks(me, gain) == 1, str(b.stacks(me, gain)))
    for _ in range(skills._num(skills.BUFFS[debuff].get("TimeValue"), 1)):
        b.tick(unit)
    check("debuff ran out", b.stacks(me, debuff) == 0)
    limit = skills._num(skills.BUFFS[gain].get("OverlayLimit"), 99) or 99
    stacking = skills._num(skills.BUFFS[gain].get("OverlayType"), 2) in (1, 3)
    want = min(limit, 3) if stacking else 1
    check("the two stacks that ran out count too", b.stacks(me, gain) == want, f"{b.stacks(me, gain)} of {want}")

    b, pid = battle(skill_id)
    me = ("p", pid)
    b.add_buff(me, plain, 1, pid)
    b.remove_buff(me, plain)
    check("losing a buff that is no debuff gives nothing", b.stacks(me, gain) == 0)
    other = max(p for p in b.cards if b.team_of(p) == b.team_of(pid))
    b.add_buff(("p", other), debuff, 2, pid)
    b.remove_buff(("p", other), debuff)
    check("a teammate's debuff gives nothing", b.stacks(me, gain) == 0)

    skills.LOSE_DEBUFF_EVENT = False
    try:
        b, pid = battle(skill_id)
        b.add_buff(("p", pid), debuff, 2, pid)
        b.remove_buff(("p", pid), debuff)
        check("switch off: nothing", b.stacks(("p", pid), gain) == 0)
    finally:
        skills.LOSE_DEBUFF_EVENT = True

    print(f"\n{sum(checks)}/{len(checks)} ok")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
