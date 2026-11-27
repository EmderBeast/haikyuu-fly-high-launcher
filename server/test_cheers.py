"""Offline test of the Team Cheers (cheers.py), captured account in a temporary folder:
  - star order: activation x00, steps x01..x05, next star x10 ... top x60 (client NextStarUpCfg)
  - 0x2504: Fragment cost taken, 0x2505 with the new star_id before the reply; not enough -> FAILED;
    kept in 0x2501 after a new login
  - 0x0F13 milestone gifts; 0x0F09 wish refused for a pool without WishMenu
  - match room: one ATTR_MORALE per line-up cheer = SupportAttribute key 7 of its star (captured rooms:
    60000 each for the captured account's x60 cheers)
  python test_cheers.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-cheers-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402,F401
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import cheers as C  # noqa: E402
import matches  # noqa: E402

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


def chain(cheer):
    out, s = [], None
    while True:
        s = C.next_star(cheer, s)
        if s is None:
            return out
        out.append(s)


async def main():
    ch = chain(1)
    check("star order 100, 101..105, 110 ... 160", ch[:7] == [100, 101, 102, 103, 104, 105, 110] and ch[-1] == 160
          and len(ch) == 37, ch[:8])
    check("2435 -> 2440, 160 is the top", C.next_star(24, 2435) == 2440 and C.next_star(1, 160) is None)

    acc = accounts.Account(9851, accounts.CAPTURED)
    c = FakeClient(acc)
    have = C.owned(acc)
    cheer = next(k for k, v in sorted(have.items()) if C.next_star(k, v))
    target = C.next_star(cheer, have[cheer])
    for item, n in C.cost(target).items():
        acc.set_item(item, n)
    await C.handle_star(c, P.PlayerSupportUpGradeStarC2S(conf_id=cheer).SerializeToString())
    ids = [m for m, _ in c.sent]
    check("0x2504: items, 0x2505 new star, reply", ids[-2:] == [C.MSG_UPDATE, C.MSG_STAR]
          and P.PlayerSupportUpdateS2C.FromString(c.sent[-2][1]).datas[0].star_id == target
          and P.PlayerSupportUpGradeStarS2C.FromString(c.sent[-1][1]).star_id == target
          and all(acc.item_count(i) == 0 for i in C.cost(target)), (ids, target))
    c.sent.clear()
    for item in C.cost(C.next_star(cheer, target)):
        acc.set_item(item, 0)
    await C.handle_star(c, P.PlayerSupportUpGradeStarC2S(conf_id=cheer).SerializeToString())
    check("0x2504 without the items: FAILED", P.PlayerSupportUpGradeStarS2C.FromString(c.sent[-1][1]).error == P.FAILED
          and C.owned(acc)[cheer] == target)

    new = next(k for k in sorted(C.CHEERS) if k not in C.owned(acc) and C.cost(k * 100))
    for item, n in C.cost(new * 100).items():
        acc.set_item(item, n)
    await C.handle_star(c, P.PlayerSupportUpGradeStarC2S(conf_id=new).SerializeToString())
    check("0x2504 activates an unowned cheer at x00", C.owned(acc).get(new) == new * 100)

    again = accounts.Account(9851, accounts.CAPTURED)
    ids = {d.star_id for d in P.PlayerSupportInfoS2C.FromString(C.info(again, again.b.pb("0x2501"))).datas}
    check("new login: 0x2501 with both changes", target in ids and new * 100 in ids and len(ids) == len(have) + 1)

    unit = C.units(acc)[9701]
    unit.total_count, unit.ClearField("gift_received")
    unit.total_count = 12
    C._save_unit(acc, unit)
    gold, exp = acc.get_currency(100), acc.item_count(131) if not acc.is_currency(131) else acc.get_currency(131)
    c.sent.clear()
    await C.handle_gift(c, P.LotterySupportGetGiftC2S(id=9701).SerializeToString())
    u = C.units(acc)[9701]
    exp2 = acc.item_count(131) if not acc.is_currency(131) else acc.get_currency(131)
    check("0x0F13: milestones 5 and 10 (131 x500, gold x50000), not 15",
          list(u.gift_received) == [5, 10] and acc.get_currency(100) - gold == 50000 and exp2 - exp == 500,
          list(u.gift_received))
    check("0x0F13: unit pushed (0x0F07) before the reply", [m for m, _ in c.sent][-2:] == [C.MSG_LOTTERY_INFO, C.MSG_GIFT])
    await C.handle_wish(c, P.LotterySupportWishC2S(id=9701, item_id=404000004).SerializeToString())
    check("0x0F09: pool without WishMenu refused", P.LotterySupportWishS2C.FromString(c.sent[-1][1]).err == P.FAILED)

    team = P.PVPTeamInfo()
    form = P.TeamFormation()
    form.player_supports.extend([2, 5, 0])
    C.team_morale(team, acc, form)
    want = [C.kv(C.STARUP[C.owned(acc)[s]]["SupportAttribute"]).get(7) for s in (2, 5)]
    check("room morale: one entry per line-up cheer, SupportAttribute key 7",
          [a.val for a in team.attr if a.type == P.ATTR_MORALE] == want and want == [60000, 60000]
          and list(team.formation.player_supports) == [2, 5, 0], want)


if __name__ == "__main__":
    if not C.STARUP:
        print("skip: tables missing (gamedata)")
    else:
        asyncio.run(main())
        print(f"{sum(checks)}/{len(checks)} checks passed", TMP)
        raise SystemExit(0 if all(checks) else 1)
