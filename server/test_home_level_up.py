"""Offline test of the base level up (0x2303 HomeLevelUpC2S, see homestead.handle_home_level_up): Emre's
new account (28.11.2026) got an empty reply, the success banner could not be closed and the base stayed Lv.1.
Temporary accounts, no port.
  python test_home_level_up.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-homelevel-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import homestead  # noqa: E402
from missions import Missions  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def level_up(c):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_LEVEL_UP](c, b"")
    return P.HomeLevelUpS2C.FromString(c.sent[-1][1])


async def main():
    c = FakeClient(991001)
    c.account = accounts.Account(991001, accounts.CAPTURED)
    c.missions = Missions(c.account)
    d = homestead.entry(c)
    # as Emre's new account: base Lv.1, comfort 70 (basic 60 + a table 10)
    d["level"], d["comfort"] = 1, 70
    cost = homestead.HOME_LEVELS[2]["LevelUpConsume"]
    item, number = cost[1], cost[3]
    c.account.set_currency(item, number + 5)
    r = await level_up(c)
    check("reply: level 2, no error", r.err == 0 and r.level == 2, str(r).replace("\n", " "))
    check("comfort = new basic 85 + furniture 10", r.comfort.total == 95, r.comfort.total)
    check("cost taken (homesteadlevel row 2 LevelUpConsume)", c.account.get_currency(item) == 5)
    check("order: currency change, then the reply", [m for m, _ in c.sent] == [0x0405, homestead.MSG_LEVEL_UP])
    li = P.HomeInfoS2C.FromString(homestead.patch_info(c, c.account.b.pb("0x2301"))).base_info
    check("login HomeInfo: level 2 and comfort 95", li.level == 2 and li.comfort.total == 95)
    check("rooms see the new base level", homestead._base_info(c).base_info.level == 2)
    r = await level_up(c)
    check("not enough coins: refused, nothing taken", r.err != 0 and c.account.get_currency(item) == 5)
    d["level"] = max(homestead.HOME_LEVELS)
    r = await level_up(c)
    check("highest level: refused", r.err != 0)
    # outdoor expansion (0x2305): row expansion_times + 1, base level >= LevelLimit, ExpansionConsume
    d["level"], d["expansion"] = 1, 0
    row = homestead.EXPANSIONS[1]
    c.account.set_currency(227, 10 ** 6)
    c.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_EXPAND](c, b"")
    r = P.HomeOutdoorSpaceExpandS2C.FromString(c.sent[-1][1])
    check("expansion below LevelLimit: refused", r.err != 0 and c.account.get_currency(227) == 10 ** 6, row["LevelLimit"])
    d["level"] = row["LevelLimit"]
    c.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_EXPAND](c, b"")
    r = P.HomeOutdoorSpaceExpandS2C.FromString(c.sent[-1][1])
    check("expansion: times 1, cost taken", r.err == 0 and r.expansion_times == 1
          and c.account.get_currency(227) == 10 ** 6 - row["ExpansionConsume"][3])
    li = P.HomeInfoS2C.FromString(homestead.patch_info(c, c.account.b.pb("0x2301"))).base_info
    check("login HomeInfo: expansion_times 1", li.expansion_times == 1)

    # planning players into a schedule (0x2307)
    lvl = sorted(homestead.LEVELS)[0]
    c.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_PLAN](c, P.HomeSchedulePlanC2S(schedule_level_id=lvl, players=[5104, 5501]).SerializeToString())
    up = P.HomeUpdateS2C.FromString(c.sent[0][1])
    sch = next((x for x in up.schedules if x.schedule_level_id == lvl), None)
    check("plan: update with the schedule running, then the empty reply",
          [m for m, _ in c.sent] == [homestead.MSG_UPDATE, homestead.MSG_PLAN] and sch is not None
          and list(sch.player_conf_ids) == [5104, 5501] and sch.schedule_next_reward_time > 0)
    check("plan: planned players have 300 energy", all(p.physical == 300 for p in up.players if p.conf_id in (5104, 5501)))
    other = sorted(homestead.LEVELS)[1]
    await server.MESSAGE_HANDLERS[homestead.MSG_PLAN](c, P.HomeSchedulePlanC2S(schedule_level_id=other, players=[5501]).SerializeToString())
    check("a player moved to another schedule leaves the first", homestead.entry(c)["schedules"][str(lvl)]["players"] == [5104])

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
