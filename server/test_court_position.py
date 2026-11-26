"""Offline test of the triggers that ask where a player stands (skills._value):
  ComeOnPlayer     on the court right now (tests in the game, captures lev2211 / 19.11.: after a
                   sub-out the parts stop, the NIF parts run again)
  PlayerPosition   7 front row, 8 back row, no number = on the bench (skill texts)
  ToPosition       4 comes onto the court, 3 leaves it, 1 / 2 comes into the front / back row
and that the state for a re-simulation (snapshot / restore) keeps rows and stamina counters.
  python test_court_position.py
"""
import engine
import matches
import skills

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def main():
    if not skills.AVAILABLE:
        print("skill tables missing: nothing to test")
        return
    c = matches._captured('0x0302', P.PVPCreateS2C)
    b = skills.Battle(c.info, engine.stats_for(c.info))
    team = skills.HOST
    mine = sorted(p for p in b.cards if b.team_of(p) == team)
    six, bench = mine[:6], mine[6]
    a, back = six[0], six[5]
    start, touch = skills.Ctx("start"), skills.Ctx("touch")
    val = lambda key, arg, pid, ctx=touch: bool(b._value(key, arg, pid, ctx))

    def rally(court, front):
        b.was_on_court, b.was_front = b.on_court, b.front
        b.on_court, b.front = set(court), {team: set(front)}
        b.ever_on_court |= b.on_court

    rally(six, six[:3])
    check("ComeOnPlayer: on the court", val("ComeOnPlayer", 0, a))
    check("... not on the bench", not val("ComeOnPlayer", 0, bench))
    check("PlayerPosition 7: front row", val("PlayerPosition", 7, a) and not val("PlayerPosition", 7, back))
    check("PlayerPosition 8: back row", val("PlayerPosition", 8, back) and not val("PlayerPosition", 8, a))
    check("PlayerPosition 7 / 8: not for the bench",
          not val("PlayerPosition", 7, bench) and not val("PlayerPosition", 8, bench))
    check("PlayerPosition without a number: on the bench", val("PlayerPosition", 0, bench) and not val("PlayerPosition", 0, a))
    check("ToPosition 4 at the match start", val("ToPosition", 4, a, start) and not val("ToPosition", 4, bench, start))
    check("ToPosition 1 / 2 at the match start: his row",
          val("ToPosition", 1, a, start) and not val("ToPosition", 2, a, start)
          and val("ToPosition", 2, back, start) and not val("ToPosition", 1, back, start))
    check("ToPosition only at the rally start", not val("ToPosition", 4, a, touch))

    rally(six, six[:3])  # nothing changes
    check("same places: no ToPosition", not any(val("ToPosition", n, a, start) for n in (1, 2, 3, 4)))

    rally(six, six[1:4])  # rotation: a goes to the back row, six[3] to the front row
    check("rotation: ToPosition 2 for who goes back", val("ToPosition", 2, a, start) and not val("ToPosition", 1, a, start))
    check("... ToPosition 1 for who comes to the front", val("ToPosition", 1, six[3], start))
    check("... no ToPosition 4 / 3", not val("ToPosition", 4, a, start) and not val("ToPosition", 3, a, start))

    rally([bench] + six[1:], six[1:4])  # a is substituted
    check("sub-out: ToPosition 3", val("ToPosition", 3, a, start) and not val("ToPosition", 4, a, start))
    check("sub-in: ToPosition 4", val("ToPosition", 4, bench, start) and not val("ToPosition", 3, bench, start))
    check("sub-out: ComeOnPlayer is over", not val("ComeOnPlayer", 0, a))
    check("... PlayerPosition without a number holds", val("PlayerPosition", 0, a))
    skills.COME_ON_NOW = False
    try:
        check("switch off: ComeOnPlayer stays after a sub-out", val("ComeOnPlayer", 0, a))
    finally:
        skills.COME_ON_NOW = True
    rally([bench] + six[1:], six[1:4])
    check("a rally later: no ToPosition 3 again", not val("ToPosition", 3, a, start))

    # --- the state a re-simulation goes back to
    b.spend(six[1], 20)
    snap = b.snapshot()
    b.spend(six[1], 30)
    b.spend(six[1], -10)
    rally(six, six[:3])
    b.restore(snap)
    check("restore: rows", b.front == {team: set(six[1:4])} and b.was_front == {team: set(six[1:4])})
    check("restore: stamina counters", b.spent.get(team) == 20 and not b.healed.get(six[1]), str(b.spent))

    print(f"\n{sum(checks)}/{len(checks)} ok")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
