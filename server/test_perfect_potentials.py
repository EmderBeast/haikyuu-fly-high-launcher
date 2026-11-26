"""Offline test of the Admin Mode switch "perfect_potentials" (admin.sync_perfect): every card gets the 6
potentials of its potential guide (playerlist SpecialtySuit / SpecialtyProperty), the old ones come off; a
second sync changes nothing; switched off, the created potentials are gone and every card wears its old ones
again. Also via the login (server.handle_login) and the in-game command /perfect. Temporary accounts.
  python test_perfect_potentials.py
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-perfect-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
import wire  # noqa: E402

GID = 9992
admin.CONFIG = TMP / "admin.json"
admin.CONFIG.write_text("{}", encoding="utf-8")  # Admin Mode exists on this server
admin.MULTIPLAYER = TMP / "multiplayer.json"  # own server
admin.ACCOUNTS = TMP
admin.UNLOCKED = TMP / "admin_unlocked.json"
admin.UNLOCKED.write_text(json.dumps({str(GID): time.time()}), encoding="utf-8")
admin.QUEUE, admin.DONE = TMP / "admin_queue", TMP / "admin_done"
admin.ALL_QUEUE, admin.LAST_LOGIN = admin.QUEUE / "all.json", TMP / "last_login.json"
admin.ADMINS = TMP / "admins.json"


def spec_of(acc, sid):
    return wire.parse(acc.get_specialty(sid))


def check_perfect(acc):
    cards = admin._all_cards(acc)
    n = 0
    for inst, card in cards.items():
        conf = wire.get(wire.parse(card), 3, 0)
        plan = admin.perfect_set(conf)
        if not plan:
            continue
        slots = admin._slots(card)
        for p in plan:
            sid = slots[p["position"]]
            f = spec_of(acc, sid)
            assert wire.get(f, 2, 0) == p["conf"] and wire.get(f, 6, 0) == inst, (inst, p, f)
            assert wire.get(wire.parse(wire.get(f, 7, b"")), 1, 0) == p["main"]
            assert wire.get(f, 3, 0) == p["level"]
        n += 1
    return n


async def main():
    assert admin.is_admin(GID)
    # the guide of Hinata (1101): sets 502 (4x) + 503 (2x), stats 4 then 1
    plan = admin.perfect_set(1101)
    assert [p["conf"] // 100 for p in plan] == [30502] * 4 + [30503] * 2
    assert [p["main"] for p in plan if p["position"] in (1, 3, 5)] == [1022, 1025, 1025]
    assert all(sum(lv for _, lv in p["vices"]) == p["level"] // 3 for p in plan)

    acc = accounts.Account(GID, accounts.CAPTURED)
    before = {i: admin._slots(c) for i, c in admin._all_cards(acc).items()}
    old_owned = {sid: i for i, s in before.items() for sid in s if sid}
    assert old_owned, "the captured account wears potentials"

    acc.admin_flags[admin.PERFECT] = True
    changed = admin.sync_perfect(acc)
    n = check_perfect(acc)
    print("cards changed", changed, "checked", n, "potentials", len(acc.all_specialties()))
    assert changed == n > 100
    for sid in old_owned:  # the old potentials are free now
        assert wire.get(spec_of(acc, sid), 6, 0) == 0
    count = len(acc.specialties)
    assert admin.sync_perfect(acc) == 0 and len(acc.specialties) == count  # nothing twice

    acc.admin_flags[admin.PERFECT] = False
    assert admin.sync_perfect(acc) == changed
    assert "perfect" not in acc.admin_flags
    after = {i: admin._slots(c) for i, c in admin._all_cards(acc).items()}
    assert after == before  # every card wears its old potentials again
    for sid, inst in old_owned.items():
        assert wire.get(spec_of(acc, sid), 6, 0) == inst
    assert not any(sid >= admin.SPECIALTY_INST_BASE for s in after.values() for sid in s)

    # through the game: /perfect on, login, the login sends the cards and the new potentials
    class C:
        def __init__(self):
            self.gid, self.sent, self.writer = GID, [], None

        async def send_packet(self, m, p, encrypted=True, compress=False):
            self.sent.append((m, p))

        async def send_raw(self, m, f, b):
            self.sent.append((m, None))
    line, msg, _, _ = admin.command(GID, "/perfect on")
    assert "queued" in line, line
    c = C()
    await server.handle_login(c, wire.build([(1, 0, GID), (7, 2, b"token")]))
    assert c.account.admin_flags.get(admin.PERFECT) is True
    assert check_perfect(c.account) > 0 and "perfect" in c.account.admin_flags
    print("OK", TMP)


asyncio.run(main())
