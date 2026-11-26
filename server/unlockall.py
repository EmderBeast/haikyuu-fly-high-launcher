"""
Admin Mode "unlock everything" (admin.py queue entries {"unlock_all": true} / {"unlock": [kinds]} /
{"relock": [kinds]}, commands /unlockall and /unlock <kind> ... [off]).

Kinds:
  cards       one card of every character and one of every memory not owned yet (admin "all_cards")
  max         every card 6 stars / level 80 / full resonance, memories level 80 / tier V (admin "max_cards")
  items       every bag item kind to 999 (admin "items")
  currency    gold, diamonds, EXP, stamina raised to the Admin Mode window's amounts (never lowered)
  coaches     one coach of every coachlist row the player has none of, top level, locked, its job buffs
              drawn as a level-up draws them (coach.py)
  potentials  the switch "perfect potentials from the guide" for every card (admin.sync_perfect)
  cheers      every Team Cheer (supportconf) at its top star
  cosmetics   every profile item of playerinfoitemconfig: portraits (ItemType 1), portrait frames (2),
              banners (3), badges (4), titles (5), CG gallery (6), stickers (7), voices / scenes (8),
              wallpapers (10), chat frames (11)

cheers and cosmetics are switches on the account (admin_flags["unlocked"]), not copied id lists: at every
login the ids are taken from the tables the server runs on (edition.py: global or jp), so the client never
gets an id its own tables do not know, and "relock" takes them away again.

From the tables / captures:
  - PersonalInfoS2C.item_ids (0x2101) holds owned profile items of every ItemType: the captured account has
    1218 of the 2121 rows, of all ten types, also hidden ones (Hide = 1); PersonalUpdateS2C (0x2102) carries
    the full list again (personal.py);
  - PlayerSupportInfoS2C.datas[].star_id (0x2501) = supportstarupconf id = cheer * 100 + star * 10 + step;
    the captured account holds 26 cheers, the top ones at <cheer>60 (MaxStar 6).
Assumed (tables only, not checked in the game): the client accepts every row of both tables as owned,
including rows whose OpenTime has not come and cheers the captured account never had; a top-star cheer needs
nothing else from the server. Not built: what cheers do in a match.
Left out on purpose (side effects or no server state): home furniture / garden, bonds and collections
(IllustratedInfoS2C: their rewards are stats), achievements, emojis.
"""
import random

import pvp

KINDS = ("cards", "max", "items", "currency", "coaches", "potentials", "cheers", "cosmetics")
SWITCHES = ("cheers", "cosmetics")  # kept as account switches, built from the tables at every login
NAMES = {"cards": "Karten und Memories", "max": "alles auf Maximum", "items": "Gegenstände", "currency": "Währungen",
         "coaches": "Trainer", "potentials": "perfekte Potentials", "cheers": "Cheers", "cosmetics": "Kosmetik"}
ALIASES = {"karten": "cards", "charaktere": "cards", "memories": "cards", "maximum": "max", "gegenstände": "items",
           "gegenstaende": "items", "währung": "currency", "waehrung": "currency", "währungen": "currency",
           "geld": "currency", "trainer": "coaches", "coach": "coaches", "potential": "potentials",
           "cheer": "cheers", "anfeuern": "cheers", "kosmetik": "cosmetics", "cosmetic": "cosmetics",
           "profil": "cosmetics"}
CURRENCY = {100: 9_999_999, 108: 999_999, 102: 9_999_999, 105: 999}  # as the Admin Mode window's defaults
ITEM_COUNT = 999
MSG_PERSONAL, MSG_SUPPORT = 0x2101, 0x2501


def kind_of(word: str) -> str | None:
    word = word.strip().lower()
    return word if word in KINDS else ALIASES.get(word)


def cosmetic_ids() -> list[int]:
    """Every profile item the running tables know."""
    return sorted(r["ID"] for r in pvp._cfg("playerinfoitemconfig"))


def cheer_star_ids() -> list[int]:
    """The top star id (supportstarupconf) of every cheer in supportconf."""
    stars = {}
    for r in pvp._cfg("supportstarupconf"):
        stars[r["ID"] // 100] = max(stars.get(r["ID"] // 100, 0), r["ID"])
    return sorted(stars[c["ID"]] for c in pvp._cfg("supportconf") if c["ID"] in stars)


def has(acc, kind: str) -> bool:
    return kind in ((getattr(acc, "admin_flags", None) or {}).get("unlocked") or [])


def item_ids(acc, base: list[int]) -> list[int]:
    """Owned profile items: the baseline's, plus every one of the tables when cosmetics are unlocked."""
    if not has(acc, "cosmetics"):
        return list(base)
    have = set(base)
    return list(base) + [i for i in cosmetic_ids() if i not in have]


def personal_info(acc, body: bytes) -> bytes:
    """PersonalInfoS2C (0x2101) with every profile item when cosmetics are unlocked, else unchanged."""
    if not has(acc, "cosmetics"):
        return body
    P = pvp.P
    msg = P.PersonalInfoS2C.FromString(body)
    ids = item_ids(acc, list(msg.item_ids))
    del msg.item_ids[:]
    msg.item_ids.extend(ids)
    return msg.SerializeToString()


def support_info(acc, body: bytes) -> bytes:
    """PlayerSupportInfoS2C (0x2501) with every cheer at its top star when cheers are unlocked."""
    if not has(acc, "cheers"):
        return body
    P = pvp.P
    return P.PlayerSupportInfoS2C(datas=[P.PlayerSupportData(star_id=i) for i in cheer_star_ids()]).SerializeToString()


def _all_coaches(acc, rng=random) -> int:
    import coach
    gid = int(acc.gid)
    owned = {c.conf_id for c in coach.cards(gid, acc).values()}
    n = 0
    for conf, row in sorted(coach.COACHES.items()):
        if conf in owned:
            continue
        level = coach.MAX_LEVEL.get(row.get("CoachRare"), 15)
        probe = coach._P().CoachCard(conf_id=conf)
        buffs = [b for b in (coach._new_buff(probe, rng) for _ in range(coach.buff_slots(level))) if b]
        coach.admin_build(gid, acc, {"conf": conf, "level": level, "buffs": [list(b) for b in buffs], "lock": True})
        n += 1
    return n


def apply(acc, kinds, apply_action) -> list[str]:
    """Unlock these kinds for the account; apply_action = admin._apply for the kinds admin.py already has."""
    kinds = [k for k in KINDS if k in set(kinds)]  # fixed order: cards before max and potentials
    done = []
    for kind in kinds:
        if kind == "cards":
            done += apply_action(acc, {"all_cards": True})
        elif kind == "max":
            done += apply_action(acc, {"max_cards": True})
        elif kind == "items":
            done += apply_action(acc, {"items": ITEM_COUNT})
        elif kind == "currency":
            want = {str(c): max(v, acc.get_currency(c)) for c, v in CURRENCY.items()}
            done += apply_action(acc, {"currency": want})
        elif kind == "coaches":
            done.append(f"{_all_coaches(acc)} Trainer dazu (jeder auf Höchststufe)")
        elif kind == "potentials":
            done += apply_action(acc, {"flags": {"perfect_potentials": True}})
        else:
            flags = acc.admin_flags.setdefault("unlocked", [])
            if kind not in flags:
                flags.append(kind)
            n = len(cheer_star_ids()) if kind == "cheers" else len(cosmetic_ids())
            done.append(f"{NAMES[kind]}: alle {n} freigeschaltet")
    return done


def relock(acc, kinds) -> list[str]:
    """Take the cheers / cosmetics switches back (the other kinds are real changes and stay)."""
    flags = acc.admin_flags.get("unlocked") or []
    done = []
    for kind in kinds:
        if kind in flags:
            flags.remove(kind)
            done.append(f"{NAMES[kind]}: wieder wie vorher")
    return done
