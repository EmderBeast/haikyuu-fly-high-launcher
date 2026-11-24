"""
skilltext.py BUFF_OR_SKILL_ID...: what the game's own skill description says about a buff or skill.

For a buff id: the skills that add it (trigger, effects) and the description (English) of every skill
list entry those skills belong to. The texts come from the client's language table
(Haikyu-Fly-High-Analyse/dump/lid_en_de.json) and are the most direct statement of a rule we have.
"""
import json
import re
import sys
from pathlib import Path

import skills

LANG = Path(__file__).resolve().parents[2] / "Haikyu-Fly-High-Analyse" / "dump" / "lid_en_de.json"
_lid = json.loads(LANG.read_text(encoding="utf-8")) if LANG.exists() else {}
KEEP = ("Type", "TimePoint", "Trigger", "SkillEffect", "CDType", "CDValue", "SkillTagType", "BeforeSkill", "AfterSkill")


def text(tag) -> str:
    m = re.search(r"(\d+)", str(tag or ""))
    v = _lid.get(m.group(1)) if m else None
    t = (v or {}).get("en") if isinstance(v, dict) else None
    if t and re.fullmatch(r"[0-9a-f]{20,}", t):  # some entries are hex-encoded UTF-8
        try:
            t = bytes.fromhex(t).decode("utf-8")
        except ValueError:
            pass
    return t or ""


def lists_of(skill_id: int) -> list[dict]:
    """Skill list rows (a card's skill at a level, a bond, ...) that contain the skill; highest level."""
    best = {}
    for row in skills.SKILLLIST.values():
        if skill_id in skills._list(row.get("Skillid")):
            key = row.get("Name")
            if key not in best or skills._num(row.get("Skilllevel")) > skills._num(best[key].get("Skilllevel")):
                best[key] = row
    return list(best.values())


def describe(row: dict) -> str:
    t = text(row.get("SkillIntro"))
    for i, n in enumerate(row.get("SkillIntroNumber") or []):
        t = t.replace("{%d}" % i, str(n))
    return f"{text(row.get('Name'))} (list {row['ID']}, level {row.get('Skilllevel')}): {t}"


def adders(buff_id: int) -> list[int]:
    out = []
    for sid, s in skills.SKILLS.items():
        for e in skills._list(s.get("SkillEffect")):
            if not isinstance(e, dict) or not str(e.get("Key", "")).startswith("AddBuff"):
                continue
            v1 = e.get("V1")
            if v1 == buff_id or ("ByLevel" in e["Key"] and isinstance(v1, int) and 0 <= buff_id - v1 < 10
                                 and skills.BUFFS.get(v1, {}).get("Type") == skills.BUFFS.get(buff_id, {}).get("Type")):
                out.append(sid)
    return list(dict.fromkeys(out))


def show(x: int):
    b = skills.BUFFS.get(x)
    if b:
        print("BUFF", x, {k: v for k, v in b.items() if v not in (None, skills.NONE, "") and k != "ID"})
    sids = adders(x) if b else []
    if x in skills.SKILLS and x not in sids:
        sids.append(x)
    for sid in sids:
        s = skills.SKILLS[sid]
        print("  SKILL", sid, json.dumps({k: s[k] for k in KEEP if s.get(k) not in (None, skills.NONE, "")},
                                        ensure_ascii=False)[:700])
        rows = lists_of(sid)
        if not rows:  # a lent skill: the armed buffs that carry it, and who gives those
            for bid, row in skills.BUFFS.items():
                if skills._num(row.get("Type")) == 7 and (skills._flat(row.get("Args")) or [0])[0] == sid:
                    for giver in adders(bid):
                        print("    LENT by armed buff", bid, "from skill", giver)
                        rows += [r for r in lists_of(giver) if r not in rows]
        for row in rows:
            print("    TEXT", describe(row)[:1400])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for arg in sys.argv[1:]:
        show(int(arg))
