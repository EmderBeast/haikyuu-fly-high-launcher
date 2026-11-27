"""Offline test of the memories (recalling.py) on the captured account (temporary folder):
  - 0x3C04 level up: exp of consumed memories (NumExp) and exp items, gold (NumGold + item gold), cap at
    max_level; breakthrough (only inst_id) raises max_level 20 -> 40 for its recallingarrt cost
  - 0x3C05 stage up with a copy, gold from recallingskill; 0x3C08 dismantle (507001001 x NumExp // 100)
  - 0x3C0A auto list on / off; kept after a new login (0x3C01 setting, removed memories in 0x3C02 field 3)
  python test_recalling.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-recalling-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402,F401  (hooks)
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import recalling as R  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, acc):
        self.gid, self.sent, self.writer, self.account = acc.gid, [], None, acc

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


def new(acc, conf, **kw):
    raw = acc.add_recalling(conf, **kw)
    return P.PlayerRecalling.FromString(raw)


def conf_of_star(s):
    return next(i for i, r in sorted(R.RECALLING.items()) if r.get("Star") == s)


async def main():
    acc = accounts.Account(9861, accounts.CAPTURED)
    c = FakeClient(acc)
    acc.set_currency(R.GOLD, 10 ** 8)
    s3 = conf_of_star(3)
    main_rec, f1, f2 = new(acc, s3), new(acc, s3), new(acc, conf_of_star(2))
    acc.set_item(507001001, 5)
    inst_item = acc.items[507001001]["inst_id"]
    gold0 = acc.get_currency(R.GOLD)
    req = P.RecallingUpgradeLevelC2S(inst_id=main_rec.inst_id, consume_inst_ids=[f1.inst_id, f2.inst_id])
    req.items.add(inst_id=inst_item, number=3)
    await R.handle_level(c, req.SerializeToString())
    r = R.get(acc, main_rec.inst_id)
    exp_in = R.LEVELUP[(3, 1)]["NumExp"] + R.LEVELUP[(2, 1)]["NumExp"] + 300
    t = P.PlayerRecalling(conf_id=s3, level=1, max_level=20)
    R.level_up(t, exp_in)
    check("0x3C04: level from NumExp of both + 3 x 100 item exp", (r.level, r.exp) == (t.level, t.exp) and r.level > 1,
          (r.level, r.exp))
    gold = R.STAR_GOLD[3] + R.STAR_GOLD[2] + 3 * 200
    check("0x3C04: gold NumGold 6000 + 2000 + 3 x 200, items taken",
          gold0 - acc.get_currency(R.GOLD) == gold and acc.item_count(507001001) == 2, gold0 - acc.get_currency(R.GOLD))
    check("0x3C04: fodder gone, 0x3C02 then reply", R.get(acc, f1.inst_id) is None
          and [m for m, _ in c.sent][-2:] == [R.MSG_UPDATE, R.MSG_LEVEL]
          and list(P.RecallingUpdateS2C.FromString(c.sent[-2][1]).recalling_removes) == [f1.inst_id, f2.inst_id])

    big = new(acc, s3, level=19, max_level=20)
    big_f = [new(acc, conf_of_star(4)) for _ in range(2)]
    await R.handle_level(c, P.RecallingUpgradeLevelC2S(inst_id=big.inst_id, consume_inst_ids=[f.inst_id for f in big_f]).SerializeToString())
    b = R.get(acc, big.inst_id)
    check("0x3C04: stops at max_level 20, extra exp lost", (b.level, b.max_level, b.exp) == (20, 20, 0), b)
    step = R.breakthrough(b)
    for item, n in step[1]:
        if item != R.GOLD:
            acc.set_item(item, n)
    c.sent.clear()
    await R.handle_level(c, P.RecallingUpgradeLevelC2S(inst_id=big.inst_id).SerializeToString())
    b = R.get(acc, big.inst_id)
    check("0x3C04 breakthrough: max_level 20 -> 40, items used", b.max_level == 40 and step[0] == 40
          and all(acc.item_count(i) == 0 for i, _ in step[1] if i != R.GOLD), step)
    c.sent.clear()
    await R.handle_level(c, P.RecallingUpgradeLevelC2S(inst_id=big.inst_id).SerializeToString())
    check("breakthrough below the cap is refused", P.RecallingUpgradeLevelS2C.FromString(c.sent[-1][1]).err == P.FAILED)
    check("breakthrough chain 20 -> 40 -> 60 -> 70 -> 80", [R.breakthrough(P.PlayerRecalling(
        conf_id=s3, level=m, max_level=m))[0] for m in (20, 40, 60, 70)] == [40, 60, 70, 80])

    dup = new(acc, s3)
    gold0 = acc.get_currency(R.GOLD)
    await R.handle_stage(c, P.RecallingUpgradeStageC2S(inst_id=main_rec.inst_id, consume_inst_ids=[dup.inst_id]).SerializeToString())
    r = R.get(acc, main_rec.inst_id)
    skill = R.RECALLING[s3]["SkillId"]
    check("0x3C05: stage 1 -> 2 with a copy, gold of recallingskill row SkillId*10+1",
          r.stage == 2 and gold0 - acc.get_currency(R.GOLD) == (R.SKILL[skill * 10 + 1].get("Gold") or 0)
          and R.get(acc, dup.inst_id) is None)
    other = new(acc, conf_of_star(2))
    await R.handle_stage(c, P.RecallingUpgradeStageC2S(inst_id=main_rec.inst_id, consume_inst_ids=[other.inst_id]).SerializeToString())
    check("0x3C05: another memory is no stage material", R.get(acc, other.inst_id) is not None and R.get(acc, main_rec.inst_id).stage == 2)

    d2, d3 = new(acc, conf_of_star(2)), new(acc, s3)
    before = acc.item_count(R.DECOMP_ITEM)
    await R.handle_decomp(c, P.RecallingDeComposeC2S(inst_ids=[d2.inst_id, d3.inst_id]).SerializeToString())
    gain = (R.LEVELUP[(2, 1)]["NumExp"] + R.LEVELUP[(3, 1)]["NumExp"]) // 100
    check("0x3C08: 10 + 30 exp items", acc.item_count(R.DECOMP_ITEM) - before == gain == 40, gain)
    await R.handle_decomp(c, P.RecallingDeComposeC2S(inst_ids=[main_rec.inst_id]).SerializeToString())
    check("0x3C08: levelled memory not dismantled", R.get(acc, main_rec.inst_id) is not None)

    base_list = R.auto_list(acc)
    await R.handle_auto(c, P.RecallingAutoResolveC2S(ids=[s3], unids=base_list[:1]).SerializeToString())
    setting = list(P.RecallingAutoResolveS2C.FromString(c.sent[-1][1]).setting)
    check("0x3C0A: whole list back, one off, one on", setting == base_list[1:] + [s3], setting)

    gone_base = next(i for i, raw in acc.b.recallings.items() if P.PlayerRecalling.FromString(raw).level <= 1
                     and P.PlayerRecalling.FromString(raw).stage <= 1 and not P.PlayerRecalling.FromString(raw).player_inst_id
                     and not P.PlayerRecalling.FromString(raw).locked and R.star(P.PlayerRecalling.FromString(raw)) in (2, 3))
    await R.handle_decomp(c, P.RecallingDeComposeC2S(inst_ids=[gone_base]).SerializeToString())
    again = accounts.Account(9861, accounts.CAPTURED)
    check("new login: auto list in 0x3C01", list(P.RecallingInfoS2C.FromString(R.info(again, again.b.pb("0x3C01"))).setting) == setting)
    upd = [p for m, p in again.login_updates() if m == R.MSG_UPDATE]
    check("new login: dismantled baseline memory removed (0x3C02 field 3)",
          upd and gone_base in P.RecallingUpdateS2C.FromString(upd[0]).recalling_removes and gone_base not in again.all_recallings())
    check("new login: stage kept", R.get(again, main_rec.inst_id).stage == 2)


if __name__ == "__main__":
    if not R.RECALLING:
        print("skip: tables missing (gamedata)")
    else:
        asyncio.run(main())
        print(f"{sum(checks)}/{len(checks)} checks passed", TMP)
        raise SystemExit(0 if all(checks) else 1)
