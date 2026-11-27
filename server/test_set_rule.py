"""Offline test of who gets the set and when the AI casts (engine.SET_RULE). Captures, AI side: 73 % of
the spikes are spike ultimates (the set goes to a player whose spike ultimate is ready), and block /
dig ultimates are cast whenever ready (455 of 468, 335 of 338). Here: AI against AI on a captured
room, a few matches; the share of ultimate spikes must be well above the old coin flip (38 %).
  python test_set_rule.py
"""
import random

import engine
import livematch
import matches
import skills

P = matches.P


def share(rule: bool) -> float:
    engine.SET_RULE = rule
    random.seed(5)
    spikes = ults = 0
    for _ in range(4):
        c = matches._captured('0x0302', P.PVPCreateS2C)
        c.info.preset_match_id = 0
        m = livematch.LiveMatch(c.info, 0.5)
        m.auto_skill = True
        for _ in range(12):
            if m.over:
                break
            for raw in m.next_rally():
                f = P.FrameData.FromString(raw)
                if f.HasField("round_data") and f.round_data.HasField("hit_data") and f.round_data.hit_data.formula_type == engine.SPIKE:
                    h = f.round_data.hit_data
                    spikes += 1
                    ults += skills._num(skills.SKILLS.get(h.skill_id, {}).get("Type")) == 6
    engine.SET_RULE = True
    return ults / max(spikes, 1)


def main():
    if not skills.AVAILABLE:
        print("skill tables missing: nothing to test")
        return
    new, old = share(True), share(False)
    ok = new >= 0.6 and new > old
    print(("ok   " if ok else "FAIL ") + f"ultimate spikes: {new:.0%} with the rule, {old:.0%} without (captures 73 %)")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
