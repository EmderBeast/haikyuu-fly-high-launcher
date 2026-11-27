"""Offline test of Admin Mode "unlock everything" (unlockall.py; admin.py queue entries "unlock_all", "unlock",
"relock", commands /unlockall and /unlock): after the next login the player's PersonalInfoS2C (0x2101) lists
every profile item of the running tables and nothing else, PlayerSupportInfoS2C (0x2501) every cheer at its top
star, the account has every character, coach, the items and currencies; single kinds and taking the switches
back work; a player without rights on a shared server gets nothing. Temporary accounts, no port.
  python test_admin_unlock.py
"""
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import accounts

sys.stdout.reconfigure(encoding="utf-8")  # names carry a star; a redirected console is cp1252

TMP = Path(tempfile.mkdtemp(prefix="hfh-adminunlock-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
admin.MULTIPLAYER = TMP / "multiplayer.json"
admin.ACCOUNTS, admin.UNLOCKED, admin.ADMINS = TMP, TMP / "admin_unlocked.json", TMP / "admins.json"
admin.QUEUE, admin.DONE, admin.LAST_LOGIN = TMP / "admin_queue", TMP / "admin_done", TMP / "last_login.json"
admin.ALL_QUEUE = admin.QUEUE / "all.json"
import coach  # noqa: E402
import gameconfig  # noqa: E402
import mail  # noqa: E402
import matches  # noqa: E402
import personal  # noqa: E402
import unlockall  # noqa: E402
import wire  # noqa: E402

P = matches.P
GID, OTHER, THIRD = 9893, 9894, 9895
PW = "geheim"
FAILED = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILED.append(what)


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def gift(c, code):
    await server.MESSAGE_HANDLERS[admin.MSG_GIFT_CODE](c, wire.build([(1, 2, code.encode("utf-8"))]))


def items(c):
    return list(P.PersonalInfoS2C.FromString(c.last(0x2101)).item_ids)


def cheers(c):
    raw = c.last(0x2501)  # None: the baseline's message went out unchanged (send_raw)
    return sorted(d.star_id for d in P.PlayerSupportInfoS2C.FromString(
        c.account.b.pb("0x2501") if raw is None else raw).datas)


def report(gid):
    return json.loads((admin.DONE / f"{gid}.json").read_text(encoding="utf-8"))["done"]


async def main():
    table_items = sorted(r["ID"] for r in pvp._cfg("playerinfoitemconfig"))
    table_cheers = {r["ID"]: r for r in pvp._cfg("supportconf")}
    stars = {r["ID"] for r in pvp._cfg("supportstarupconf")}
    top = unlockall.cheer_star_ids()
    check("tables: profile items and cheers are there", len(table_items) > 1000 and len(table_cheers) > 20,
          f"{len(table_items)} items, {len(table_cheers)} cheers")
    check("one top star id per cheer, each a row of supportstarupconf with no higher one",
          len(top) == len(table_cheers) and all(i in stars and i // 100 in table_cheers
                                                and not any(s // 100 == i // 100 and s > i for s in stars) for i in top))
    # the captured account's top cheers have exactly these ids (e.g. 160, 260): star 6 = <cheer>60
    captured = {d.star_id for d in P.PlayerSupportInfoS2C.FromString(accounts.CAPTURED.pb("0x2501")).datas}
    check("the captured top cheers carry the same ids", len(captured & set(top)) >= 10, f"{len(captured & set(top))}")

    admin.CONFIG.write_text(json.dumps({"pw_sha256": hashlib.sha256(PW.encode()).hexdigest()}), encoding="utf-8")
    c = await login(GID)
    acc = c.account
    base_items, base_cheers = items(c), cheers(c)
    base_cards, base_gold = len(acc.owned_conf_ids()), acc.get_currency(100)
    check("a new player owns only a part", len(base_items) < len(table_items) and len(base_cheers) < len(top),
          f"{len(base_items)} items, {len(base_cheers)} cheers, {base_cards} characters")
    await gift(c, PW)

    # --- single kinds: /unlock cheers kosmetik ---
    await gift(c, "/unlock cheers kosmetik")
    q = json.loads((admin.QUEUE / f"{GID}.json").read_text(encoding="utf-8"))
    check("/unlock queues the kinds (German word understood)", q == [{"unlock": ["cheers", "cosmetics"]}], str(q))
    c = await login(GID)
    acc = c.account
    got = items(c)
    check("0x2101 lists every profile item of the tables, each once, nothing unknown",
          sorted(got) == table_items and got[:len(base_items)] == base_items, f"{len(got)}")
    check("0x2501 lists every cheer at its top star", cheers(c) == top, f"{len(cheers(c))}")
    check("nothing else changed", len(acc.owned_conf_ids()) == base_cards and acc.get_currency(100) == base_gold
          and not coach.cards(GID, acc))
    upd = P.PersonalUpdateS2C.FromString(personal.update(GID, acc))
    check("0x2102 (sent with time-limited unlocks) keeps every item", sorted(upd.item_ids) == table_items)
    check("the report names both", any("Cheers: alle" in r for r in report(GID))
          and any("Kosmetik: alle" in r for r in report(GID)), str(report(GID)))
    c = await login(GID)
    check("kept over the next login", sorted(items(c)) == table_items and cheers(c) == top)

    # --- taking the switches back ---
    await gift(c, "/unlock cosmetics off")
    c = await login(GID)
    check("/unlock cosmetics off: the old items again, cheers stay", items(c) == base_items and cheers(c) == top)
    await gift(c, "/unlock cheers aus")
    c = await login(GID)
    check("/unlock cheers aus: the old cheers again", cheers(c) == base_cheers)
    before = len(mail._box(GID)["mails"])
    await gift(c, "/unlock")
    await gift(c, "/unlock quatsch")
    await gift(c, "/unlock cards off")
    texts = [m["content"] for m in mail._box(GID)["mails"][before:]]
    check("wrong /unlock commands queue nothing and list the kinds", not (admin.QUEUE / f"{GID}.json").exists()
          and len(texts) == 3 and all("cheers" in t and "cosmetics" in t for t in texts), str(texts)[:120])

    # --- everything: /unlockall ---
    await gift(c, "/unlockall")
    c = await login(GID)
    acc = c.account
    rep = report(GID)
    print("report:", rep)
    check("no step failed", not any(r.startswith("Fehler") for r in rep))
    check("every profile item and every cheer", sorted(items(c)) == table_items and cheers(c) == top)
    wanted = {conf for conf in gameconfig.PLAYERS if conf < admin.TWIN_BASE}
    check("every character", wanted <= acc.owned_conf_ids(), f"{len(acc.owned_conf_ids())} of {len(wanted)}")
    cards = [P.PlayerCard.FromString(raw) for raw in admin._all_cards(acc).values()]
    check("every card at 6 stars and the level cap", all(k.star == admin.MAX_STAR and k.level == gameconfig.level_cap(
        admin.MAX_STAR) for k in cards), f"{len(cards)} cards")
    mine = coach.cards(GID, acc)
    check("one coach of every coachlist row, top level, locked, a buff per slot",
          sorted(k.conf_id for k in mine.values()) == sorted(coach.COACHES)
          and all(k.level == coach.MAX_LEVEL[coach.COACHES[k.conf_id]["CoachRare"]] and k.locked
                  and len(k.job_buffs) == coach.buff_slots(k.level) for k in mine.values()), f"{len(mine)}")
    check("every coach buff is one its coach can roll",
          all(b.buff_id in coach.COACHES[k.conf_id]["CoachBuffId"] and b.job in coach.JOBS
              for k in mine.values() for b in k.job_buffs))
    check("the coaches are in CoachInfoS2C", len(P.CoachInfoS2C.FromString(c.last(coach.MSG_INFO)).coach_cards) == len(mine))
    check("currencies raised", all(acc.get_currency(cid) >= v for cid, v in unlockall.CURRENCY.items()))
    check("perfect potentials switched on", bool(acc.admin_flags.get(admin.PERFECT)))
    check("items set", any(f"Gegenstände auf {unlockall.ITEM_COUNT}" in r for r in rep))

    # a second /unlockall adds no second set of coaches and never lowers a currency
    acc.set_currency(100, 20_000_000)
    acc.save()
    await gift(c, "/unlockall")
    c = await login(GID)
    check("again: no second coaches, gold not lowered", len(coach.cards(GID, c.account)) == len(coach.COACHES)
          and c.account.get_currency(100) == 20_000_000)

    # --- the queue entry for another player; a player without rights ---
    o = await login(OTHER)
    admin.queue(OTHER, [{"unlock": ["cosmetics"]}])
    o = await login(OTHER)
    check("queue entry for another player: cosmetics only", sorted(items(o)) == table_items and cheers(o) == base_cheers)
    admin.MULTIPLAYER.write_text("{}", encoding="utf-8")
    admin.save_admins({"owner": str(GID), "admins": {}})
    t = await login(THIRD)
    await gift(t, "/unlockall")
    check("no rights on a shared server: nothing queued", not (admin.QUEUE / f"{THIRD}.json").exists())
    t = await login(THIRD)
    check("... and nothing unlocked", items(t) == base_items and cheers(t) == base_cheers)
    print("FAILED: " + ", ".join(FAILED) if FAILED else "OK", TMP)
    raise SystemExit(1 if FAILED else 0)


asyncio.run(main())
