"""[SCORING MACHINE] (5701 ultimate 57014111) also counts as a Quick Spike when the team's Lineup Specialty is Block
or Quick ATK (skills.EXTRA_TAGS). Club boss capture (decoded/match_msgs.json): with Quick ATK, every such spike
gave Korai +3 [Well-Rounded] (57013151), which is full (10) after the third rally.
  python test_extra_tags.py
"""
from pathlib import Path

import calibrate as C
import skills

CAPTURE = Path(__file__).resolve().parents[2] / "game-capture-setup" / "decoded" / "match_msgs.json"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class _B:  # just enough of a Battle for _tags_used
    def __init__(self, genre):
        self.genre = {0: genre, 1: 4}

    team_of = staticmethod(lambda pid: 0)


for genre, want in ((1, {0, 1}), (2, {0, 1}), (0, {0}), (3, {0})):
    ctx = skills.Ctx("touch", actor=0, formula=5, skill_id=57014111)
    got = skills.Battle._tags_used(_B(genre), ctx)
    check(f"Lineup Specialty {genre}: tags {sorted(want)}", got == want, sorted(got))
ctx = skills.Ctx("touch", actor=0, formula=5, skill_id=10301)
check("other skills keep their one tag", skills.Battle._tags_used(_B(1), ctx) == {skills._stag(skills.SKILLS.get(10301, {}))})

if CAPTURE.exists():
    info, rallies = C.matches_in(CAPTURE)[6]
    stacks = []
    orig = skills.Battle.rally_end

    def rally_end(self, *a, **k):
        stacks.append(self.stacks(("p", 0), 57013151))
        return orig(self, *a, **k)
    skills.Battle.rally_end = rally_end
    C.replay(info, rallies, [])
    skills.Battle.rally_end = orig
    check("club boss replay: [Well-Rounded] full (10) by the third rally as on Garena", stacks[2] == 10 and stacks[-1] == 10, stacks)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
raise SystemExit(0 if all(checks) else 1)
