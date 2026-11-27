"""Offline test of the potentials (specialty.py), temporary accounts:
  - the level-up rule against the client's preview numbers (specialtyupconf Exp / UpExp / UpGold / Gold)
  - sub-attributes at levels 3/6/9/12: a 3-sub-attribute potential ends at level 12 with 4 of them whose
    levels add up to 3, a 4-sub-attribute one with 4 adding up to 4 (as all captured level-12 ones)
  - 0x1803 consumes, charges gold/essence, sends REMOVE + UPDATE + reply; locked material refused
  - 0x1806 lock, 0x1808 dismantle (Resolve reward), 0x1809 / 0x180A settings, all kept after a new login
  python test_specialty.py
"""
import asyncio
import random
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-specialty-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import specialty as S  # noqa: E402
import matches  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid):
    import wire
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def fresh(acc, conf=3010345, vices=3):
    """A level-0 rare-4 potential of the account (copy of a captured one)."""
    s = P.PlayerSpecialty(inst_id=acc._new_inst(), conf_id=conf, main_attr=P.PlayerSpecialtyAttr(attr_id=1023))
    for a in [2000, 2001, 2002, 2003][:vices]:
        s.vice_attrs.add(attr_id=a)
    S.put(acc, s)
    return s


def rules():
    up = S.UP
    s = P.PlayerSpecialty(conf_id=3010345, main_attr=P.PlayerSpecialtyAttr(attr_id=1023))
    s.vice_attrs.add(attr_id=2000)
    gold = S.level_up(s, 600 + 800, 0, random.Random(1))  # level 0 needs 600, then 800 of 1600
    want = int(600 / 600 * 4000) + int(800 / 1600 * 6000)
    check("exp over two levels, gold prorated per level as the client shows it",
          (s.level, s.exp, s.main_attr.attr_lev, gold) == (1, 800, 1, want), (s.level, s.exp, gold, want))
    gold = S.level_up(s, 10, 2400, random.Random(1))
    check("consumed potentials' Gold is taken off, never below 0", gold == 0 and s.exp == 810)
    check("max level by rare (specialtyfunc 7)", [S.max_level(r) for r in range(1, 6)] == [6, 6, 9, 12, 12])
    check("sub-attribute levels for rare 4", S.vice_levels(4) == [3, 6, 9, 12])
    for start, total in ((3, 3), (4, 4)):
        for seed in range(30):
            t = P.PlayerSpecialty(conf_id=3010345, main_attr=P.PlayerSpecialtyAttr(attr_id=1023))
            for a in [2000, 2001, 2002, 2003][:start]:
                t.vice_attrs.add(attr_id=a)
            S.level_up(t, 10 ** 7, 0, random.Random(seed))
            ids = [v.attr_id for v in t.vice_attrs]
            ok = (t.level == 12 and t.exp == 0 and len(ids) == 4 and len(set(ids)) == 4
                  and sum(v.attr_lev for v in t.vice_attrs) == total)
            if not ok:
                break
        check(f"level 12 from {start} sub-attributes: 4 of them, levels add up to {total}", ok, ids)
    check("worth of a level-1 rare-4 potential = Exp 1460, Gold 2400",
          S.worth(P.PlayerSpecialty(conf_id=3010345, level=1)) == (1460, 2400))
    check("dismantle reward = Resolve (essence 127 x Exp, gold 100 x Gold)",
          S.resolve(P.PlayerSpecialty(conf_id=3010345, level=1)) == [(127, 1460), (100, 2400)]
          and S.resolve(P.PlayerSpecialty(conf_id=3010345)) == [(127, 980)])
    assert up


async def flows():
    c = FakeClient(9871)  # the captured account as baseline (its potentials), saved in the temporary folder
    c.account = acc = accounts.Account(9871, accounts.CAPTURED)
    acc.set_currency(S.GOLD, 10 ** 7)
    acc.set_currency(S.ESSENCE, 1000)
    main, a, b = fresh(acc), fresh(acc), fresh(acc, vices=4)
    gold0, ess0 = acc.get_currency(S.GOLD), acc.get_currency(S.ESSENCE)
    c.sent.clear()
    await S.handle_up_level(c, P.PlayerSpecialtyUpLevelC2S(main_inst_id=main.inst_id, consume_inst_ids=[a.inst_id, b.inst_id],
                                                           consume_essence=100).SerializeToString(), random.Random(3))
    ids = [m for m, _ in c.sent]
    after = S.get(acc, main.inst_id)
    # 980 + 980 + 100 = 2060 exp: level 0 -> 1 (600), 1460 of level 1's 1600
    check("0x1803: 2 x 980 + 100 essence -> level 1 with 1460 exp", (after.level, after.exp) == (1, 1460), after)
    gold = int(600 / 600 * 4000) + int(1460 / 1600 * 6000)
    check("0x1803: gold and essence charged",
          acc.get_currency(S.GOLD) == gold0 - gold and acc.get_currency(S.ESSENCE) == ess0 - 100)
    check("0x1803: consumed ones gone", S.get(acc, a.inst_id) is None and S.get(acc, b.inst_id) is None)
    check("0x1803: currency change, REMOVE, UPDATE, reply", ids == [0x0405, S.MSG_UPDATE, S.MSG_UPDATE, S.MSG_UP_LEVEL]
          and P.PlayerSpecialtyUpdateS2C.FromString(c.sent[1][1]).opt == S.REMOVE
          and P.PlayerSpecialtyUpdateS2C.FromString(c.sent[2][1]).opt == S.UPDATE, ids)

    x = fresh(acc)
    c.sent.clear()
    await S.handle_lock(c, P.PlayerSpecialtyLockC2S(inst_id=x.inst_id, lock=True).SerializeToString())
    check("0x1806: locked, UPDATE + reply", S.get(acc, x.inst_id).locked and [m for m, _ in c.sent] == [S.MSG_UPDATE, S.MSG_LOCK])
    c.sent.clear()
    await S.handle_up_level(c, P.PlayerSpecialtyUpLevelC2S(main_inst_id=main.inst_id, consume_inst_ids=[x.inst_id]).SerializeToString())
    check("0x1803: a locked potential is not consumed",
          S.get(acc, x.inst_id) is not None and P.PlayerSpecialtyUpLevelS2C.FromString(c.sent[-1][1]).err == P.FAILED)
    await S.handle_decomp(c, P.PlayerSpecialtyDecompC2S(inst_ids=[x.inst_id]).SerializeToString())
    check("0x1808: a locked potential is not dismantled", S.get(acc, x.inst_id) is not None)

    y = fresh(acc)
    base = next(i for i in acc.b.specialties if not P.PlayerSpecialty.FromString(acc.b.specialties[i]).locked
                and not P.PlayerSpecialty.FromString(acc.b.specialties[i]).player_inst_id)
    ess = acc.get_currency(S.ESSENCE)
    c.sent.clear()
    await S.handle_decomp(c, P.PlayerSpecialtyDecompC2S(inst_ids=[y.inst_id, base]).SerializeToString())
    worth = 980 + S.resolve(P.PlayerSpecialty.FromString(acc.b.specialties[base]))[0][1]
    check("0x1808: dismantled, essence credited", S.get(acc, y.inst_id) is None and S.get(acc, base) is None
          and acc.get_currency(S.ESSENCE) == ess + worth, (acc.get_currency(S.ESSENCE) - ess, worth))
    check("0x1808: REMOVE first, reply last", c.sent[0][0] == S.MSG_UPDATE and c.sent[-1][0] == S.MSG_DECOMP)

    await S.handle_decomp_set(c, P.PlayerSpecialtyDecompSetC2S(
        decomp_auto_set=P.PlayerSpecialtDecompSet(rare=3, setting=5)).SerializeToString())
    rep = P.PlayerSpecialtyDecompSetS2C.FromString(c.sent[-1][1])
    check("0x1809: echoed", (rep.decomp_auto_set.rare, rep.decomp_auto_set.setting) == (3, 5))
    await S.handle_filter(c, P.PlayerSpecialtyFilterSetingC2S(set_type=P.FilterSeting_Add, set=P.PlayerSpecialtyFilterSeting(
        name="Mine", data=P.SpecialtyFilterSetingData(set_list=[101]))).SerializeToString())
    new = P.PlayerSpecialtyFilterSetingS2C.FromString(c.sent[-1][1]).set
    check("0x180A: new scheme gets an id and a time", new.id and new.create and new.name == "Mine")

    c2 = FakeClient(9871)
    c2.account = accounts.Account(9871, accounts.CAPTURED)  # loaded again from the saved file
    info = P.PlayerSpecialtyInfoS2C.FromString(S.info(c2.account, c2.account.b.pb("0x1801")))
    have = {s.inst_id for s in info.specialtys}
    check("new login: dismantled baseline potential gone from 0x1801", base not in have)
    check("new login: settings kept", any(d.rare == 3 and d.setting == 5 for d in info.decomp_auto_set)
          and any(f.id == new.id for f in info.filter_seting)
          and len(info.filter_seting) == len(P.PlayerSpecialtyInfoS2C.FromString(acc.b.pb("0x1801")).filter_seting) + 1)
    lvl = S.get(c2.account, main.inst_id)
    check("new login: level kept", (lvl.level, lvl.exp) == (1, 1460))
    await S.handle_filter(c2, P.PlayerSpecialtyFilterSetingC2S(set_type=P.Del, set=new).SerializeToString())
    check("0x180A: scheme deleted", all(f.id != new.id for f in S.filters(c2.account)))

    c3 = await login(9873)  # a starter account: the login sends its own 0x1801 once settings exist
    await S.handle_decomp_set(c3, P.PlayerSpecialtyDecompSetC2S(
        decomp_auto_set=P.PlayerSpecialtDecompSet(rare=2, setting=1)).SerializeToString())
    c4 = await login(9873)
    raw = [p for m, p in c4.sent if m == 0x1801]
    check("login sync: 0x1801 with the account's settings", len(raw) == 1 and any(
        d.rare == 2 and d.setting == 1 for d in P.PlayerSpecialtyInfoS2C.FromString(raw[0]).decomp_auto_set))


if __name__ == "__main__":
    if not S.ROWS:
        print("skip: tables missing (gamedata)")
    else:
        rules()
        asyncio.run(flows())
        print(f"{sum(checks)}/{len(checks)} checks passed", TMP)
        raise SystemExit(0 if all(checks) else 1)
