"""Offline test of the home schedules (0x2314 HomeScheduleRewardRefreshC2S) against the numbers of the
Garena captures (see homestead.py). Temporary accounts, no port.
  python test_homestead.py
"""
import asyncio
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-home-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import homestead  # noqa: E402
import wire  # noqa: E402

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


async def main():
    # one earning of every captured group (level, players, amount per 15 minutes)
    captured = [
        (100104, [5401, 5502, 5501, 1216], 227, 67), (100104, [5802, 5302, 1305, 1303], 227, 67),
        (100103, [1702, 1105, 1204], 227, 48), (100302, [1302, 1304], 102, 525),
        (100304, [1109, 1111, 1105, 1110], 102, 1120), (100304, [5102, 5101, 5301, 1103], 102, 1120),
        (100402, [1557, 1536], 100, 1743), (100404, [5601, 5503, 1614, 1603], 100, 3375),
        (100404, [5402, 5203, 5104, 1503], 100, 3375),
    ]
    bad = [(lv, pl, homestead.earning(lv, pl)) for lv, pl, item, n in captured if homestead.earning(lv, pl) != (item, n)]
    check("one earning as in the captures (9 groups)", not bad, str(bad))
    check("every 900 s, 6 energy", homestead.step_of(100104) == (900, 6))

    # the run of decrypted_peak2111 / peak2111b: energy 24 -> 0 in four earnings, then it stops
    t0 = 1795223440
    d = {"schedules": {"100104": {"players": [5802, 5302, 1305, 1303], "first": t0, "last": t0 + 900, "item": 227,
                                   "amount": 134, "running": True}},
         "players": {str(p): {"physical": 24, "reward": False} for p in (5802, 5302, 1305, 1303)}}
    n = homestead.advance(d, t0 + 1800)
    u = homestead.update(d)
    s = u.schedules[0]
    check("third earning: 201, energy 18, next in 900 s",
          n == 1 and s.rewards[0].number == 201 and u.players[0].physical == 18
          and s.schedule_time == t0 + 1800 and s.schedule_next_reward_time == t0 + 2700)
    check("physical_time = when the energy runs out", u.players[0].physical_time == t0 + 1800 + 3 * 900,
          str(u.players[0].physical_time))
    homestead.advance(d, t0 + 99999)
    u = homestead.update(d)
    s = u.schedules[0]
    check("stops with the energy: 402 after six earnings, no next time",
          s.rewards[0].number == 402 and s.schedule_time == t0 + 4500 and s.schedule_next_reward_time == 0,
          f"{s.rewards[0].number} at {s.schedule_time - t0}")
    check("players end at energy 0 with the time of the last earning",
          all(p.physical == 0 and p.physical_time == t0 + 4500 for p in u.players))
    check("nothing more afterwards", homestead.advance(d, t0 + 999999) == 0)

    # a refresh over the wire: HomeUpdateS2C, then the reply naming the schedule
    c = FakeClient(990501)
    await server.handle_login(c, wire.build([(1, 0, 990501), (7, 2, b"token")]))
    st = homestead.entry(c)
    check("state starts from the account's HomeInfoS2C", bool(st["schedules"]), str(list(st["schedules"])))
    level = int(next(iter(st["schedules"])))
    for s in st["schedules"].values():  # one earning is due
        s["running"] = True
        s["last"] = int(time.time()) - 900
        s["first"] = s["first"] or s["last"]
    st["players"] = {str(p): {"physical": 60, "reward": False} for s in st["schedules"].values() for p in s["players"]}
    before = {k: s["amount"] for k, s in st["schedules"].items()}
    c.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_REFRESH](
        c, P.HomeScheduleRewardRefreshC2S(schedule_level_id=level).SerializeToString())
    check("order: HomeUpdateS2C, then the reply", [m for m, _ in c.sent] == [homestead.MSG_UPDATE, homestead.MSG_REFRESH])
    reply = P.HomeScheduleRewardRefreshS2C.FromString(c.sent[1][1])
    check("reply names the schedule, no error", reply.schedule_level_id == level and reply.err == 0)
    up = P.HomeUpdateS2C.FromString(c.sent[0][1])
    check("update has every schedule and every planned player",
          len(up.schedules) == len(st["schedules"])
          and {p.conf_id for p in up.players} == {p for s in st["schedules"].values() for p in s["players"]})
    gained = all(st["schedules"][k]["amount"] - before[k] == homestead.earning(int(k), s["players"])[1]
                 for k, s in st["schedules"].items())
    check("each schedule earned once, players paid 6", gained and all(p.physical == 54 for p in up.players))

    # collecting everything (0x2317): the captured sums are 50 earnings of each schedule
    c2 = FakeClient(990502)
    await server.handle_login(c2, wire.build([(1, 0, 990502), (7, 2, b"token")]))
    st2 = homestead.entry(c2)
    now = int(time.time())
    groups = {"100304": ([1109, 1105, 1103, 1110], 102), "100404": ([5302, 1305, 1311, 1303], 100),
              "100104": ([5401, 5502, 5501, 1216], 227)}
    st2["schedules"] = {k: {"players": pl, "first": now - 50 * 900, "last": now - 900, "item": item,
                            "amount": 50 * homestead.earning(int(k), pl)[1], "running": False}
                        for k, (pl, item) in groups.items()}
    st2["players"] = {str(p): {"physical": 0, "reward": False} for pl, _ in groups.values() for p in pl}
    before = {i: c2.account.get_currency(i) for i in (102, 100, 227)}
    c2.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_COLLECT](c2, b"")
    order = [m for m, _ in c2.sent]
    check("collect: HomeUpdateS2C, change, popup, empty reply",
          order[0] == homestead.MSG_UPDATE and order[-2:] == [0x0404, homestead.MSG_COLLECT] and c2.sent[-1][1] == b"",
          str(order))
    popup = {i.conf_id: i.number for i in P.NotifyRewardsS2C.FromString(c2.sent[-2][1]).items}
    check("sums as captured: 102 x56000, 100 x168750, 227 x3350", popup == {102: 56000, 100: 168750, 227: 3350}, str(popup))
    check("credited", all(c2.account.get_currency(i) - before[i] == n for i, n in popup.items()))
    up = P.HomeUpdateS2C.FromString(c2.sent[0][1])
    check("update: schedules only, without rewards and reward times",
          not up.players and len(up.schedules) == 3 and all(
              not s.rewards and not s.schedule_first_reward_time and not s.schedule_next_reward_time for s in up.schedules))
    c2.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_COLLECT](c2, b"")
    check("nothing left: only the reply", [m for m, _ in c2.sent] == [homestead.MSG_COLLECT])

    # collecting one running schedule (0x2308) as in garena4: 100404 with 7 earnings (23625 = 7 x 3375)
    pl = [5601, 5503, 1603, 1604]
    last = int(time.time()) - 100
    st2["schedules"] = {"100404": {"players": pl, "first": last - 6 * 900, "last": last, "item": 100,
                                   "amount": 23625, "running": True},
                        "100104": {"players": [5401, 5502, 5501, 1216], "first": last, "last": last, "item": 227,
                                   "amount": 469, "running": True}}
    st2["players"] = {str(p): {"physical": 192, "reward": False} for p in pl + [5401, 5502, 5501, 1216]}
    gold = c2.account.get_currency(100)
    c2.sent.clear()
    req = P.HomeScheduleRewardC2S(schedule_level_id=100404).SerializeToString()
    await server.MESSAGE_HANDLERS[homestead.MSG_COLLECT_ONE](c2, req)
    order = [m for m, _ in c2.sent]
    check("one schedule: update, change, popup, reply as in garena4",
          order[0] == homestead.MSG_UPDATE and order[-2:] == [0x0404, homestead.MSG_COLLECT_ONE], str(order))
    up = P.HomeUpdateS2C.FromString(c2.sent[0][1])
    s = up.schedules[0] if len(up.schedules) == 1 else None
    check("update: only that schedule, no rewards, first = next = last + 900, same time and players",
          not up.players and s is not None and s.schedule_level_id == 100404 and not s.rewards
          and s.schedule_first_reward_time == s.schedule_next_reward_time == last + 900
          and s.schedule_time == last and list(s.player_conf_ids) == pl, str(up).replace("\n", " ")[:300])
    popup = [(i.conf_id, i.number) for i in P.NotifyRewardsS2C.FromString(c2.sent[-2][1]).items]
    check("100 x23625 credited, reply names the schedule", popup == [(100, 23625)]
          and c2.account.get_currency(100) - gold == 23625
          and P.HomeScheduleRewardS2C.FromString(c2.sent[-1][1]).schedule_level_id == 100404)
    check("the other schedule keeps its stock", st2["schedules"]["100104"]["amount"] == 469)
    c2.sent.clear()
    await server.MESSAGE_HANDLERS[homestead.MSG_COLLECT_ONE](c2, req)
    check("nothing earned: FAILED", P.HomeScheduleRewardS2C.FromString(c2.sent[-1][1]).err == homestead.FAILED)

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
