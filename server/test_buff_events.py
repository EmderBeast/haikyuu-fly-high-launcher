"""Offline test of skill rules that no captured match shows (read from the skill texts; used by
Garena rows and by the JP players 5605 / 5703 / 5704):
  IGetBuff / BuffNumberDecrease     a skill of the holder runs when that buff comes / shrinks
  AddBuffByIGetBuff / ...ILoseDeBuff  one stack per stack that came / went
  RemoveRandomTypeBuff / RemoveAllTypeBuff  debuffs (BuffType2 1) are taken away
  AddBuffByTeamEnergyCost           one stack per N stamina the team spent
  AddBuffByPropertyCostByLevel 136  one stack per N stamina the owner got back
  PlayerSkillCDType, TeamEnergy     trigger values
  stamina buffs                     share of the current stamina (ArgsType 3), rows with a duration
The skills are made up here from real buffs, so the test does not depend on a player's rows.
  python test_buff_events.py
"""
import engine
import matches
import skills

P = matches.P
N = skills._num
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def battle():
    c = matches._captured('0x0302', P.PVPCreateS2C)
    b = skills.Battle(c.info, engine.stats_for(c.info))
    return b, min(b.cards)


def pick(want):
    return next(i for i, r in sorted(skills.BUFFS.items()) if want(r))


def stat(r):  # a plain stat buff on the player himself (debuffs may carry a tag: the JP tables tag them all)
    return (N(r.get("Type")) == 2 and N(r.get("MountType"), 1) == 1
            and (skills._tag(r) is None or N(r.get("BuffType2")) == 1)
            and skills._flat(r.get("ModifyTarget")) != [skills.STAMINA] and N(r.get("ArgsType"), 1) == 1)


def main():
    if not skills.AVAILABLE:
        print("skill tables missing: nothing to test")
        return
    stacking = lambda r: N(r.get("OverlayType")) == 1 and N(r.get("OverlayLimit"), 0) >= 5
    debuff = pick(lambda r: stat(r) and N(r.get("BuffType2")) == 1 and stacking(r))
    debuff2 = pick(lambda r: stat(r) and N(r.get("BuffType2")) == 1 and r["ID"] != debuff)
    gain = pick(lambda r: stat(r) and N(r.get("BuffType2"), 0) != 1 and stacking(r))
    gain2 = pick(lambda r: stat(r) and N(r.get("BuffType2"), 0) != 1 and stacking(r) and r["ID"] != gain)
    print(f"debuffs {debuff} {debuff2}, buffs {gain} {gain2}")
    touch = skills.Ctx("touch")

    # --- removal by type
    b, pid = battle()
    me = ("p", pid)
    b.add_buff(me, debuff, 3, pid)
    b.add_buff(me, debuff2, 1, pid)
    b.add_buff(me, gain, 2, pid)
    b._effects_of({"ID": -1, "SkillEffect": [{"Key": "RemoveRandomTypeBuff", "V1": 1, "V2": 1, "V3": 1}]}, pid, 1, touch)
    left = b.stacks(me, debuff) + b.stacks(me, debuff2)
    check("RemoveRandomTypeBuff 1: one debuff stack gone", left == 3, str(left))
    check("... the buff stays", b.stacks(me, gain) == 2)
    b._effects_of({"ID": -2, "SkillEffect": [{"Key": "RemoveAllTypeBuff", "V1": 1, "V2": 2147483647, "V3": 1}]}, pid, 1, touch)
    check("RemoveAllTypeBuff: no debuff left", b.stacks(me, debuff) + b.stacks(me, debuff2) == 0)
    check("... the buff stays", b.stacks(me, gain) == 2)

    # --- a buff comes / shrinks
    old = {k: set(v) for k, v in skills.WATCHED.items()}
    skills.WATCHED["getbuff"].add(debuff)
    skills.WATCHED["buffdown"].add(debuff)
    try:
        b, pid = battle()
        me = ("p", pid)
        b.skills[pid] = [
            ({"ID": -3, "Type": 9, "Trigger": [{"Key": "IGetBuff", "Args": debuff, "Cmp": "IF"}],
              "SkillEffect": [{"Key": "AddBuffByIGetBuff", "V1": debuff, "V2": gain}]}, 1),
            ({"ID": -4, "Type": 9, "Trigger": [{"Key": "BuffNumberDecrease", "Args": debuff, "Cmp": "IF"}],
              "SkillEffect": [{"Key": "AddBuffByILoseDeBuff", "V1": debuff, "V2": gain2}]}, 1)]
        b.add_buff(me, debuff, 3, pid)
        check("IGetBuff + AddBuffByIGetBuff: 3 stacks came, 3 given", b.stacks(me, gain) == 3, str(b.stacks(me, gain)))
        check("... nothing for the other skill yet", b.stacks(me, gain2) == 0)
        b.add_buff(me, debuff2, 1, pid)
        check("another buff does not count", b.stacks(me, gain) == 3)
        b.remove_buff(me, debuff, 2)
        check("BuffNumberDecrease + AddBuffByILoseDeBuff: 2 went, 2 given", b.stacks(me, gain2) == 2,
              str(b.stacks(me, gain2)))
        skills.BUFF_COUNT_EVENT = False
        b.add_buff(me, debuff, 1, pid)
        check("switch off: nothing", b.stacks(me, gain) == 3)
    finally:
        skills.BUFF_COUNT_EVENT = True
        skills.WATCHED.update(old)

    # --- stamina counters
    b, pid = battle()
    me = ("p", pid)
    mate = max(p for p in b.cards if b.team_of(p) == b.team_of(pid))
    cost = {"ID": -5, "SkillEffect": [{"Key": "AddBuffByTeamEnergyCost", "V1": 10, "V2": gain, "V3": 1}]}
    b._effects_of(cost, pid, 1, touch)
    check("AddBuffByTeamEnergyCost: nothing spent, nothing given", b.stacks(me, gain) == 0)
    b.spend(pid, 12)
    b.spend(mate, 13)
    b._effects_of(cost, pid, 1, touch)
    check("25 spent by the team: 2 stacks", b.stacks(me, gain) == 2, str(b.stacks(me, gain)))
    b._effects_of(cost, pid, 1, touch)
    check("not counted twice", b.stacks(me, gain) == 2)
    b.spend(mate, 5)
    b._effects_of(cost, pid, 1, touch)
    check("the rest is kept: 30 spent, 3 stacks", b.stacks(me, gain) == 3, str(b.stacks(me, gain)))
    heal = {"ID": -6, "SkillEffect": [{"Key": "AddBuffByPropertyCostByLevel", "V1": 136, "V2": 320, "V3": 20,
                                       "V4": gain2, "V5": 1}]}
    b.spend(pid, 50)
    b._effects_of(heal, pid, 1, touch)
    check("AddBuffByPropertyCostByLevel: spending gives nothing", b.stacks(me, gain2) == 0)
    b.spend(pid, -45)
    b._effects_of(heal, pid, 1, touch)
    check("45 regained: 2 stacks", b.stacks(me, gain2) == 2, str(b.stacks(me, gain2)))

    # --- stamina buffs that are no plain amount (rows without RevertAddition: the change stays)
    def stamina(want):
        return next((i for i, r in sorted(skills.BUFFS.items()) if N(r.get("Type")) == 2
                     and skills._flat(r.get("ModifyTarget")) == [skills.STAMINA]
                     and N(r.get("RevertAddition"), 0) <= 0 and want(r)), None)

    share = stamina(lambda r: N(r.get("ArgsType")) == 3 and skills._flat(r.get("Args"))[0] == skills.STAMINA)
    timed = stamina(lambda r: N(r.get("ArgsType"), 1) == 1 and N(r.get("Time"), 0) > 0
                    and skills._flat(r.get("Args"))[0] != 0)
    b, pid = battle()
    if share:
        pct = skills._flat(skills.BUFFS[share]["Args"])[1] / 10000
        b.energy[pid] = 100.0
        b.add_buff(("p", pid), share, 1, pid)
        check(f"stamina buff {share}: {pct:+.0%} of the stamina he has", abs(b.energy[pid] - 100 * (1 + pct)) < 1e-6,
              str(b.energy[pid]))
    if timed:
        amount = skills._flat(skills.BUFFS[timed]["Args"])[0] / 10000
        b.energy[pid] = 60.0
        b.add_buff(("p", pid), timed, 1, pid)
        want = max(0.0, min(skills.ENERGY_MAX, 60 + amount))
        check(f"stamina buff {timed} with a duration: {amount:+.0f}", abs(b.energy[pid] - want) < 1e-6,
              str(b.energy[pid]))
        skills.STAMINA_FORMS = False
        try:
            b.energy[pid] = 60.0
            b.add_buff(("p", pid), timed, 1, pid)
            check("switch off: stamina unchanged", b.energy[pid] == 60.0)
        finally:
            skills.STAMINA_FORMS = True

    # --- trigger values
    b, pid = battle()
    team = b.team_of(pid)
    b.on_court = set(sorted(p for p in b.cards if b.team_of(p) == team)[:6])
    full = sum(b.energy.get(p, skills.ENERGY_MAX) for p in b.court(team))
    check("TeamEnergy = stamina of the players on court", b._value("TeamEnergy", 0, pid, touch) == full, str(full))
    b.spend(b.court(team)[0], 30)
    check("... 30 less after spending 30", b._value("TeamEnergy", 0, pid, touch) == full - 30)
    with_cd = next(i for i, r in sorted(skills.SKILLS.items()) if N(r.get("PlayerSkillCDType")) == 1)
    ctx = skills.Ctx("touch")
    ctx.skill_id = with_cd
    check("PlayerSkillCDType of the touch's skill", b._value("PlayerSkillCDType", 0, pid, ctx) == 1)
    ctx.skill_id = 0
    check("... 0 for a plain touch", b._value("PlayerSkillCDType", 0, pid, ctx) == 0)

    print(f"\n{sum(checks)}/{len(checks)} ok")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
