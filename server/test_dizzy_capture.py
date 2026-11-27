"""Offline test against the Garena captures of account Dizzy (captures/dizzy2711, marks.txt), temporary folder:
  A3  potential +0 -> +3 with essence only: 4900 essence, 18000 gold, one sub-attribute +1 at level 3
  A4  two legendary potentials (+3, +0) dismantled: +5880 essence, +10800 gold
  A2  selection box 105000036, first entry: box -1, one new potential of group 301014, CREATE, pop-up, reply
  B2  memory 14 (star 3) Lv1 -> 20 with 54 exp books: 10800 gold; gold before books in the messages
  B3  breakthrough 20 -> 40: 27000 gold + 7 books 507001007, books before gold
  B4  two 2-star Lv1 memories dismantled: +20 books 507001001; books, removes, pop-up, reply
  B1  memory draw pool 101 x1 and x10: tickets, 0x0F0A, 0x3C02 creates, reply; counters as captured
  D4  two Lv1 N cards dismantled (+128 EXP, +2 vouchers); auto list all N on / off; a x10 draw with N on the list
      (7 N dismantled at once: +7 vouchers, +448 EXP, conf ids in the reply)   (dizzy2711f)
  E1  card Max level (0x0905) and star up (0x0906) with costs and order; bond 1044 stays level 3; bond seen (0x1D03)
  F1  three rooms with Nishinoya as libero: team power exact; his rest the same with and without memory (dizzy2711g)
  C5  Dizzy's match room (dizzy2711d_handserve): morale per line-up cheer and the team power formula
  python test_dizzy_capture.py
"""
import asyncio
import json
import random
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-dizzy-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402,F401
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import cheers  # noqa: E402
import matches  # noqa: E402
import pack  # noqa: E402
import power  # noqa: E402
import recallgacha  # noqa: E402
import recalling as R  # noqa: E402
import specialty as S  # noqa: E402
import wire  # noqa: E402

P = matches.P
CAP = Path(__file__).parent.parent / "captures" / "dizzy2711"
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, acc):
        self.gid, self.sent, self.writer, self.account = acc.gid, [], None, acc

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))


def amount(acc, i):
    return acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i)


async def main():
    acc = accounts.Account(9821, accounts.CAPTURED)
    c = FakeClient(acc)
    c.missions = server.Missions(acc)  # card level / star ups report mission progress
    acc.set_currency(S.ESSENCE, 10 ** 7)
    acc.set_currency(S.GOLD, 10 ** 9)

    pot = P.PlayerSpecialty(inst_id=acc._new_inst(), conf_id=3010140, main_attr=P.PlayerSpecialtyAttr(attr_id=1002))
    for a in (2026, 2000, 2009, 2006):
        pot.vice_attrs.add(attr_id=a)
    S.put(acc, pot)
    e0, g0 = amount(acc, S.ESSENCE), amount(acc, S.GOLD)
    c.sent.clear()
    await S.handle_up_level(c, P.PlayerSpecialtyUpLevelC2S(main_inst_id=pot.inst_id, consume_essence=4900).SerializeToString())
    after = S.get(acc, pot.inst_id)
    check("A3: +0 -> +3 for 4900 essence and 18000 gold", (after.level, e0 - amount(acc, S.ESSENCE), g0 - amount(acc, S.GOLD))
          == (3, 4900, 18000), (after.level, e0 - amount(acc, S.ESSENCE), g0 - amount(acc, S.GOLD)))
    check("A3: one sub-attribute +1 at level 3, none new (it had 4)",
          len(after.vice_attrs) == 4 and sum(v.attr_lev for v in after.vice_attrs) == 1)
    check("A3: currency, REMOVE (empty), UPDATE, reply as captured",
          [m for m, _ in c.sent] == [0x0405, S.MSG_UPDATE, S.MSG_UPDATE, S.MSG_UP_LEVEL]
          and not P.PlayerSpecialtyUpdateS2C.FromString(c.sent[1][1]).specialtys)

    other = P.PlayerSpecialty(inst_id=acc._new_inst(), conf_id=3010140, main_attr=P.PlayerSpecialtyAttr(attr_id=1002))
    S.put(acc, other)
    e0, g0 = amount(acc, S.ESSENCE), amount(acc, S.GOLD)
    c.sent.clear()
    await S.handle_decomp(c, P.PlayerSpecialtyDecompC2S(inst_ids=[pot.inst_id, other.inst_id]).SerializeToString())
    check("A4: two dismantled: +5880 essence, +10800 gold",
          (amount(acc, S.ESSENCE) - e0, amount(acc, S.GOLD) - g0) == (5880, 10800))
    check("A4: REMOVE, currency, pop-up, reply as captured", [m for m, _ in c.sent] == [S.MSG_UPDATE, 0x0405, 0x0404, S.MSG_DECOMP])

    acc.set_item(105000036, 20)
    inst = acc.items[105000036]["inst_id"]
    before = set(acc.all_specialties())
    c.sent.clear()
    await pack.handle_item_use(c, P.PackItemUseC2S(inst_id=inst, number=1, chooses=[0]).SerializeToString())
    new = [S.get(acc, i) for i in set(acc.all_specialties()) - before]
    check("A2: box 20 -> 19, one potential of group 301014 with 3 or 4 sub-attributes",
          acc.item_count(105000036) == 19 and len(new) == 1 and new[0].conf_id // 10 == 301014
          and len(new[0].vice_attrs) in (3, 4), [(s.conf_id, len(s.vice_attrs)) for s in new])
    check("A2: box change, CREATE, pop-up with the potential, reply as captured",
          [m for m, _ in c.sent] == [0x0802, S.MSG_UPDATE, 0x0404, 0x0805]
          and P.NotifyRewardsS2C.FromString(c.sent[2][1]).items[0].conf_id == new[0].conf_id)

    kenma = P.PlayerRecalling.FromString(acc.add_recalling(14))
    acc.set_item(507001001, 100)
    req = P.RecallingUpgradeLevelC2S(inst_id=kenma.inst_id)
    req.items.add(inst_id=acc.items[507001001]["inst_id"], number=54)
    g0 = amount(acc, R.GOLD)
    c.sent.clear()
    await R.handle_level(c, req.SerializeToString())
    k = R.get(acc, kenma.inst_id)
    check("B2: 54 books -> Lv 20/20 for 10800 gold", (k.level, k.max_level, g0 - amount(acc, R.GOLD)) == (20, 20, 10800))
    check("B2: gold before books, update, reply", [m for m, _ in c.sent] == [0x0405, 0x0802, R.MSG_UPDATE, R.MSG_LEVEL])
    acc.set_item(507001007, 10)
    g0 = amount(acc, R.GOLD)
    c.sent.clear()
    await R.handle_level(c, P.RecallingUpgradeLevelC2S(inst_id=kenma.inst_id).SerializeToString())
    check("B3: breakthrough 20 -> 40 for 27000 gold and 7 books",
          (R.get(acc, kenma.inst_id).max_level, g0 - amount(acc, R.GOLD), acc.item_count(507001007)) == (40, 27000, 3))
    check("B3: books before gold, update, reply", [m for m, _ in c.sent] == [0x0802, 0x0405, R.MSG_UPDATE, R.MSG_LEVEL])

    two = [P.PlayerRecalling.FromString(acc.add_recalling(next(i for i, r in sorted(R.RECALLING.items()) if r.get("Star") == 2)))
           for _ in range(2)]
    b0 = acc.item_count(507001001)
    c.sent.clear()
    await R.handle_decomp(c, P.RecallingDeComposeC2S(inst_ids=[r.inst_id for r in two]).SerializeToString())
    check("B4: two 2-star Lv1 memories -> +20 books", acc.item_count(507001001) - b0 == 20)
    check("B4: books, removes, pop-up, reply as captured", [m for m, _ in c.sent] == [0x0802, R.MSG_UPDATE, 0x0404, R.MSG_DECOMP])

    acc.set_item(9901, 235)
    unit0 = recallgacha.unit_of(9821, acc, 101)
    share0 = recallgacha.share_of(9821, acc, 4)
    mem0 = len(acc.all_recallings())
    c.sent.clear()
    await recallgacha.handle_do(c, P.LotteryRecallDoC2S(id=101, count=1).SerializeToString(), random.Random(5))
    await recallgacha.handle_do(c, P.LotteryRecallDoC2S(id=101, count=10).SerializeToString(), random.Random(6))
    ids = [m for m, _ in c.sent]
    unit, share = recallgacha.unit_of(9821, acc, 101), recallgacha.share_of(9821, acc, 4)
    rewards = [len(P.LotteryRecallDoS2C.FromString(p).rewards) for m, p in c.sent if m == 0x0F0B]
    check("B1: tickets 235 -> 224, 11 new memories, rewards 1 + 10", acc.item_count(9901) == 224
          and len(acc.all_recallings()) - mem0 == 11 and rewards == [1, 10])
    check("B1: per draw tickets, 0x0F0A, 0x3C02, reply as captured", ids == [0x0802, 0x0F0A, 0x3C02, 0x0F0B] * 2)
    check("B1: counters + 11 (count, all_count, share all_count)",
          (unit.count - unit0.count, unit.all_count - unit0.all_count, share.all_count - share0.all_count) == (11, 11, 11))
    pools = {g: {i for i, _ in recallgacha.drops(R.pairs(recallgacha.POOLS[101][k])[0][0])}
             for g, k in (("big", "BigPool"), ("small", "SmallPool"), ("pool", "Pool"))}
    got = [r.item.conf_id for m, p in c.sent if m == 0x0F0B for r in P.LotteryRecallDoS2C.FromString(p).rewards]
    check("B1: every drawn memory from the pool's groups", all(any(i in s for s in pools.values()) for i in got)
          and {506001001, 506001009, 506001010} <= pools["pool"] | pools["small"], got)

    import cards as K
    n_cards = [P.PlayerCard.FromString(acc.add_card(3106)[1]) for _ in range(2)]
    e0, v0 = amount(acc, 102), amount(acc, 103)
    c.sent.clear()
    await K.handle_decompose(c, P.PlayerCardDecomposeC2S(inst_ids=[x.inst_id for x in n_cards]).SerializeToString())
    check("D4: two Lv1 N cards dismantled: +128 EXP, +2 Basic Voucher", (amount(acc, 102) - e0, amount(acc, 103) - v0) == (128, 2))
    check("D4: REMOVE (inst ids only), voucher, EXP, pop-up, reply as captured",
          [m for m, _ in c.sent] == [0x0902, 0x0802, 0x0405, 0x0404, K.MSG_DECOMPOSE]
          and not P.PlayerCardChangeS2C.FromString(c.sent[0][1]).players[0].info.conf_id)
    n_ids = [3106, 3105, 3104, 3103, 3102, 3201, 3203, 3210, 3212]
    await K.handle_auto(c, P.PlayerCardAutoResolveC2S(pids=n_ids).SerializeToString())
    check("D4: 0x090D all N on, whole list back", set(P.PlayerCardAutoResolveS2C.FromString(c.sent[-1][1]).setting) == set(n_ids))
    drawn = [3203, 2702, 3102, 3103, 3202, 3103, 3201, 2714, 3201, 3104]  # the captured Superior Recruit x10
    acc.draw_cards = lambda lottery, count: list(drawn)
    acc.set_item(111, 32)
    e0, v0 = amount(acc, 102), amount(acc, 103)
    c.sent.clear()
    await server.handle_lottery_do(c, wire.build([(1, 0, 1), (2, 0, 10)]))
    rep = [p for m, p in c.sent if m == 0x0F02][-1]
    ids = [v for n, _, v in wire.parse(rep) if n == 3]
    packed = []
    for blob in ids:
        pos = 0
        while pos < len(blob):
            v, pos = wire.read_varint(blob, pos)
            packed.append(v)
    check("D4: draw with N on the list: 7 N dismantled, +7 Basic Voucher, +448 EXP",
          (amount(acc, 103) - v0, amount(acc, 102) - e0) == (7, 448), (amount(acc, 103) - v0, amount(acc, 102) - e0))
    check("D4: reply names the dismantled cards by conf id, the kept ones by inst id",
          [packed[i] for i in (0, 2, 3, 5, 6, 8, 9)] == [3203, 3102, 3103, 3103, 3201, 3201, 3104]
          and all(packed[i] > 10 ** 9 for i in (1, 4, 7)), packed)
    await K.handle_auto(c, P.PlayerCardAutoResolveC2S(un_pids=n_ids).SerializeToString())
    check("D4: 0x090D all off: empty list", not P.PlayerCardAutoResolveS2C.FromString(c.sent[-1][1]).setting)

    # E1 (dizzy2711g): SAIYA SUZUKI Lv1 star 3 -> Max (Lv40) for 155 040 EXP, then 3 -> 4 stars with 3 copies
    suzuki = P.PlayerCard.FromString(acc.add_card(3102)[1])
    suzuki.star = 3
    acc.put_card(suzuki.SerializeToString())
    copies = []
    for _ in range(3):
        cp = P.PlayerCard.FromString(acc.add_card(3102)[1])
        cp.star = 3
        acc.put_card(cp.SerializeToString())
        copies.append(cp.inst_id)
    acc.set_currency(102, 10 ** 7)
    x0 = amount(acc, 102)
    c.sent.clear()
    await server.handle_card_level_up(c, wire.build([(1, 0, suzuki.inst_id)]))
    lv = P.PlayerCard.FromString(acc.get_card(suzuki.inst_id)).level
    check("E1: 0x0905 without optype = Max: Lv1 -> 40 for 155040 EXP", (lv, x0 - amount(acc, 102)) == (40, 155040),
          (lv, x0 - amount(acc, 102)))
    check("E1: 0x0905 EXP, card, reply as captured", [m for m, _ in c.sent][:3] == [0x0405, 0x0902, 0x0905])
    g0 = amount(acc, 100)
    c.sent.clear()
    await server.handle_card_star(c, wire.build([(1, 0, suzuki.inst_id), (3, 2, b"".join(wire.varint(i) for i in copies))]))
    check("E1: 0x0906 3 -> 4 stars with 3 copies for 200000 gold",
          (P.PlayerCard.FromString(acc.get_card(suzuki.inst_id)).star, g0 - amount(acc, 100)) == (4, 200000)
          and all(acc.get_card(i) is None for i in copies))
    check("E1: 0x0906 gold, REMOVE (inst ids), 0x1802 UPDATE, card, reply as captured",
          [m for m, _ in c.sent][:5] == [0x0405, 0x0902, 0x1802, 0x0902, 0x0906]
          and not P.PlayerCardChangeS2C.FromString(c.sent[1][1]).players[0].info.conf_id)
    check("E1: collection bond 1044 at records 4/4/5 is level 3 (lowest record star - 1), as Dizzy's stayed",
          power.collection_bonds(3102, {5: 10 ** 6}, {3103: 4, 3104: 4, 3102: 5}) ==
          power.collection_bonds(3102, {5: 10 ** 6}, {3103: 4, 3104: 4, 3102: 6})
          and power.collection_bonds(3102, {5: 10 ** 6}, {3103: 4, 3104: 4, 3102: 5}) !=
          power.collection_bonds(3102, {5: 10 ** 6}, {3103: 5, 3104: 5, 3102: 5}))
    import gallery
    c.sent.clear()
    await gallery.handle_fetter_see(c, P.IllustratedFetterSeeC2S(ferrer_id=[1044]).SerializeToString())
    check("bond seen: 0x1D01 {fetter_ids: [1044]} then the reply (dizzy2711g)", [m for m, _ in c.sent] == [0x1D01, 0x1D03]
          and list(P.IllustratedInfoS2C.FromString(c.sent[0][1]).fetter_ids) == [1044])

    rest = CAP / "dizzy2711g_rest_s1.json"
    if rest.exists():
        msgs = json.loads(rest.read_text(encoding="utf-8"))
        pb = lambda m: wire.body_to_pb(bytes.fromhex(m["hex"]), m["flags"])  # noqa: E731
        rec = {}
        for num, _, v in wire.parse(pb(next(m for m in msgs if m["id"] == "0x0408"))):
            if num == 2:
                f = wire.parse(v)
                rec[wire.get(f, 1, 0)] = wire.get(f, 2, 0)
        aw = list(P.IllustratedInfoS2C.FromString(pb(next(m for m in msgs if m["id"] == "0x1D01"))).collect_awarded)
        star = {d.star_id // 100: d.star_id for d in P.PlayerSupportInfoS2C.FromString(
            pb(next(m for m in msgs if m["id"] == "0x2501"))).datas}
        rooms = [P.PVPCreateS2C.FromString(pb(m)).info.home for m in msgs if m["id"] == "0x0302" and m["dir"] == "S2C"]
        team_ok, nishi = [], []
        for t in rooms:
            share = {}
            for sid in star.values():
                for kk, v in cheers.kv(cheers.STARUP[sid].get("Attribute")).items():
                    if kk < 7:
                        share[kk] = share.get(kk, 0) + v
            for s in t.formation.player_supports:
                if s in star:
                    for kk, v in cheers.kv(cheers.STARUP[star[s]].get("SupportAttribute")).items():
                        if kk < 7:
                            share[kk] = share.get(kk, 0) + v
            for kk, v in matches.coach_values(t.formation.coach_card).items():
                share[kk] = share.get(kk, 0) + v
            team_ok.append(power.team_power(t, share) == t.power)
            for x in t.formation.players[:7]:
                if x.info.conf_id == 1104:
                    card = P.PlayerCard()
                    card.CopyFrom(x.info)
                    r = x.recalling_entity.SerializeToString() if x.HasField("recalling_entity") else None
                    nishi.append(x.power - matches.card_entry(card, recalling=r, context=(rec, aw)).power)
        check("F1: team power formula exact in all 3 rooms (Nishinoya libero with / without memory, restored)",
              team_ok == [True, True, True], team_ok)
        check("F1: Nishinoya's open rest is the same with and without the memory (the memory share is right)",
              len(nishi) == 2 and nishi[0] == nishi[1], nishi)
    else:
        print("skip F1 (no captures/dizzy2711/dizzy2711g_rest_s1.json)")

    path = CAP / "dizzy2711d_handserve.json"
    if path.exists():
        msgs = json.loads(path.read_text(encoding="utf-8"))
        pb = lambda m: wire.body_to_pb(bytes.fromhex(m["hex"]), m["flags"])  # noqa: E731
        star = {d.star_id // 100: d.star_id for d in P.PlayerSupportInfoS2C.FromString(
            pb(next(m for m in msgs if m["id"] == "0x2501"))).datas}
        room = P.PVPCreateS2C.FromString(pb(next(m for m in msgs if m["id"] == "0x0302" and m["dir"] == "S2C"))).info
        t = room.home
        sup = [s for s in t.formation.player_supports if s]
        want = [cheers.kv(cheers.STARUP[star[s]]["SupportAttribute"]).get(7) for s in sup]
        check("C5: one morale entry per line-up cheer at its star", [a.val for a in t.attr if a.type == P.ATTR_MORALE] == want)
        share = {}
        for sid in star.values():
            for kk, v in cheers.kv(cheers.STARUP[sid].get("Attribute")).items():
                if kk < 7:
                    share[kk] = share.get(kk, 0) + v
        for s in sup:
            for kk, v in cheers.kv(cheers.STARUP[star[s]].get("SupportAttribute")).items():
                if kk < 7:
                    share[kk] = share.get(kk, 0) + v
        for kk, v in matches.coach_values(t.formation.coach_card).items():
            share[kk] = share.get(kk, 0) + v
        check("C5: team power = cards + cheer / coach shares + bonds + coach (Dizzy's room)",
              power.team_power(t, share) == t.power, (power.team_power(t, share), t.power))
    else:
        print("skip C5 (no captures/dizzy2711)")


if __name__ == "__main__":
    if not S.ROWS:
        print("skip: tables missing (gamedata)")
    else:
        asyncio.run(main())
        print(f"{sum(checks)}/{len(checks)} checks passed", TMP)
        raise SystemExit(0 if all(checks) else 1)
