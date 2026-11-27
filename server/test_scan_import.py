"""Offline test of loading a scan of the HFH-Bot in Admin Mode (scanimport.py, queue action {"scan": ...}):
a scanned player gets level, stars, skill levels, the memory (tier) and its 6 potentials exactly as scanned
(set, place, main stat, sub stats with their rolls), the old potentials come off; inventory potentials,
memories and coaches are created; a second import replaces the first; through the queue and the login the
perfect-potentials switch is turned off. A made-up scan in the file's format (no real account data).
  python test_scan_import.py
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-scanimport-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
import coach  # noqa: E402
import gameconfig  # noqa: E402
import scanimport  # noqa: E402
import wire  # noqa: E402

GID = 9996
admin.CONFIG = TMP / "admin.json"
admin.CONFIG.write_text("{}", encoding="utf-8")
admin.MULTIPLAYER = TMP / "multiplayer.json"
admin.ACCOUNTS = TMP
admin.UNLOCKED = TMP / "admin_unlocked.json"
admin.UNLOCKED.write_text(json.dumps({str(GID): time.time()}), encoding="utf-8")
admin.QUEUE, admin.DONE = TMP / "admin_queue", TMP / "admin_done"
admin.ALL_QUEUE, admin.LAST_LOGIN = admin.QUEUE / "all.json", TMP / "last_login.json"
admin.ADMINS = TMP / "admins.json"

PIECES = [
    {"slot": "I", "set": "Setter Dump", "lv": 12, "main": "Set", "rolls": {"PowerFlat": 1, "Strength": 4, "SetPct": 1, "Awareness": 1}},
    {"slot": "II", "set": "Status EnH.", "lv": 12, "main": "Strength", "rolls": {"QuickFlat": 2, "Strength": 2, "ServePct": 2, "SetPct": 2}},
    {"slot": "III", "set": "Setter Dump", "lv": 12, "main": "Serve", "rolls": {"SavePct": 2, "Awareness": 2, "Strength": 2, "ReceivePct": 2}},
    {"slot": "IV", "set": "Status EnH.", "lv": 12, "main": "Spirit", "rolls": {"Reflex": 2, "Spirit": 2, "BlockFlat": 2, "SetFlat": 2}},
    {"slot": "V", "set": "Setter Dump", "lv": 12, "main": "Set", "rolls": {"Reflex": 2, "AtkTech": 2, "DefTech": 2, "ServeFlat": 2}},
    {"slot": "VI", "set": "Setter Dump", "lv": 12, "main": "Set", "rolls": {"PowerPct": 2, "SaveFlat": 3, "SetPct": 1, "AtkTech": 1}},
]
SCAN = {"hfhscan": 1, "date": "2026-11-26 20:00", "tool": "HFH-Bot",
        "players": [{"id": 1102, "lv": 70, "stars": 5, "skills": {"S1": 3, "S2": 2, "S3": 1, "S4": 2},
                     "memory": {"id": 231610, "tier": 4}, "pots": PIECES}],
        "pots": [{"slot": "I", "set": "Morale Surge", "lv": 12, "main": "Power",
                  "rolls": {"PowerPct": 2, "SaveFlat": 3, "SetPct": 1, "AtkTech": 1}}],
        "memoryCopies": [{"id": 231610, "tier": 4, "lv": 60, "n": 1}, {"id": 210002, "tier": 5, "lv": 80, "n": 2}],
        "coaches": [{"id": 1, "name": "Keishin Ukai", "lv": 3, "level": 15,
                     "pas": [{"t": "pServe", "pos": "Li", "v": [5]}, {"t": "r10", "pos": "WS", "v": [10, 12]},
                             {"t": "o1", "pos": "MB", "v": [16, 2]}, {"t": "r8", "pos": "OP", "v": [30, 90]},
                             {"t": "bSave", "pos": "S", "v": [35]}]}],
        "bonds": {"1007": 1}}


def card_of(acc, conf):
    return max(((i, c) for i, c in admin._all_cards(acc).items() if wire.get(wire.parse(c), 3) == conf),
               key=lambda x: wire.get(wire.parse(x[1]), 4, 0))


async def main():
    main_id, vice_id = admin._attr_ids()
    acc = accounts.Account(GID, accounts.CAPTURED)
    inst, before = card_of(acc, 1102)
    old = [s for s in admin._slots(before) if s]
    report = scanimport.apply(acc, SCAN)
    print(report)
    inst2, card = card_of(acc, 1102)
    assert inst2 == inst
    f = wire.parse(card)
    assert (wire.get(f, 4), wire.get(f, 6)) == (70, 5)
    from accounts import _packed
    base = [gameconfig.SKILLS[1102][k] for k in ("Skill1", "Skill2", "Skill3", "Skill4")]
    assert _packed(wire.get(f, 8, b"")) == [base[0] + 2, base[1] + 1, base[2], base[3] + 1]
    rec = wire.parse(acc.recallings[wire.get(f, 12)])
    assert wire.get(rec, 2) == 55 and wire.get(rec, 5) == 4 and wire.get(rec, 3) == 60 and wire.get(rec, 8) == inst
    slots = admin._slots(card)
    for q, sid in zip(PIECES, slots):
        sp = wire.parse(acc.get_specialty(sid))
        row = admin._specialty_rows()[wire.get(sp, 2)]
        assert row["SuitID"] == {"Setter Dump": 508, "Status EnH.": 510}[q["set"]] and row["Rare"] == 4
        assert wire.get(sp, 6) == inst and wire.get(sp, 3) == 12
        vices = {wire.get(wire.parse(v), 1): wire.get(wire.parse(v), 2, 0) for n, _, v in sp if n == 8}
        assert sum(vices.values()) == sum(q["rolls"].values()) - len(q["rolls"])
    assert wire.get(wire.parse(acc.get_specialty(slots[1])), 7) and \
        wire.get(wire.parse(wire.get(wire.parse(acc.get_specialty(slots[1])), 7)), 1) == main_id[10]  # Strength
    assert wire.get(wire.parse(wire.get(wire.parse(acc.get_specialty(slots[5])), 7)), 1) == main_id[202]  # Set %
    for sid in old:  # the old pieces are free now
        assert wire.get(wire.parse(acc.get_specialty(sid)), 6, 0) == 0
    st = acc.admin_flags["scan_import"]
    assert len(st["specs"]) == 7 and len(st["recs"]) == 3 and len(st["coaches"]) == 1
    c = coach.cards(GID, acc)[st["coaches"][0]]
    assert c.conf_id == 9 and c.level == 15  # "lv" 3 = Legendary = CoachRare 4
    got = [(jb.job, coach.BUFFS[jb.buff_id]["BuffNumber"]) for jb in c.job_buffs]
    assert got == [(4, "5"), (1, "10_12"), (2, "16_2"), (5, "90_30"), (3, "35")], got
    n_specs, n_recs = len(acc.specialties), len(acc.recallings)
    scanimport.apply(acc, SCAN)  # a second import replaces the first
    assert (len(acc.specialties), len(acc.recallings)) == (n_specs, n_recs)
    import matches
    baseline = len(matches.P.CoachInfoS2C.FromString(accounts.CAPTURED.pb("0x5501")).coach_cards)
    assert len(coach.cards(GID, acc)) == baseline + 1  # the imported coach once, not twice

    # through Admin Mode: queue, login; the perfect-potentials switch goes off
    acc2 = accounts.Account(GID + 1, accounts.CAPTURED)
    admin.UNLOCKED.write_text(json.dumps({str(GID): time.time(), str(GID + 1): time.time()}), encoding="utf-8")
    admin.queue(str(GID + 1), [{"flags": {"perfect_potentials": True}}, {"scan": SCAN}])

    class C:
        def __init__(self, gid):
            self.gid, self.sent, self.writer = gid, [], None

        async def send_packet(self, m, p, encrypted=True, compress=False):
            self.sent.append((m, p))

        async def send_raw(self, m, f, b):
            self.sent.append((m, None))
    del acc2
    cl = C(GID + 1)
    await server.handle_login(cl, wire.build([(1, 0, GID + 1), (7, 2, b"token")]))
    a = cl.account
    assert a.admin_flags.get("perfect_potentials") is False and "perfect" not in a.admin_flags
    _, card = card_of(a, 1102)
    assert wire.get(wire.parse(card), 4) == 70 and len([s for s in admin._slots(card) if s]) == 6
    done = json.loads((admin.DONE / f"{GID + 1}.json").read_text(encoding="utf-8"))["done"]
    assert any("1 Spieler" in d for d in done) and any("bonds" in d for d in done), done
    print("OK", TMP)


asyncio.run(main())
