"""Offline test of the achievement gallery's point rewards (0x0E08 TaskReceiveAchievementRewardsC2S):
claimable once when the group's achievement points (currency 118 / 119 / 120 / 121) have reached the
row's Point in achievementpointreward. Temporary accounts, no port.
  python test_achievement_rewards.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-achieve-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import missions  # noqa: E402
import gameconfig  # noqa: E402
import wire  # noqa: E402

P = matches.P
DIAMOND = 108
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def claim(c, reward_id):
    """Sends the request; returns (error of the reply, ids in a TaskChangeS2C sent with it)."""
    start = len(c.sent)
    await server.handle_achievement_reward(c, P.TaskReceiveAchievementRewardsC2S(achievement_id=reward_id).SerializeToString())
    rep = P.TaskReceiveAchievementRewardsS2C()
    rep.ParseFromString(next(p for m, p in reversed(c.sent) if m == 0x0E08))
    ids = []
    for msg_id, payload in c.sent[start:]:
        if msg_id == server.MSG_TASK_CHANGE:
            ch = P.TaskChangeS2C()
            ch.ParseFromString(payload)
            ids += list(ch.achievement_rewards_state)
    return rep.error, ids


async def main():
    c = FakeClient(990601)
    await server.handle_login(c, wire.build([(1, 0, 990601), (7, 2, b"token")]))
    acc, m = c.account, c.missions
    rows = gameconfig.ACHIEVEMENT_REWARDS
    check("table loaded, every group has a points currency",
          len(rows) > 0 and all(r.get("TaskTeamId") in missions.ACHIEVEMENT_CURRENCY for r in rows.values()), f"{len(rows)} rows")
    # the group's achievements pay the currency its rewards are measured in
    pays = {}
    for t in gameconfig.TASKS.values():
        team = t.get("TaskTeamId")
        if team in missions.ACHIEVEMENT_CURRENCY:
            for item, _ in gameconfig.parse_rewards(t.get("TaskItemReward")):
                if item in missions.ACHIEVEMENT_CURRENCY.values():
                    pays.setdefault(team, set()).add(item)
    check("achievements of a group pay that group's points", all(v == {missions.ACHIEVEMENT_CURRENCY[k]} for k, v in pays.items()), str(pays))

    got = m.achievements_received()
    free = sorted((r["Point"], rid) for rid, r in rows.items() if r["TaskTeamId"] == 8001 and rid not in got)
    point, rid = free[0]
    currency = missions.ACHIEVEMENT_CURRENCY[8001]
    row = rows[rid]
    reward = gameconfig.parse_rewards(row["Reward"])
    print(f"     reward {rid}: {point} points of currency {currency} -> {reward}")

    acc.set_currency(currency, point - 1)
    err, ids = await claim(c, rid)
    check("one point short: refused, nothing sent", err != 0 and not ids and rid not in m.achievements_received())

    acc.set_currency(currency, point)
    before = {item: acc.get_currency(item) for item, _ in reward}
    err, ids = await claim(c, rid)
    check("points reached: accepted", err == 0)
    check("new state sent with the claimed id only", ids == [rid], str(ids))
    check("points are compared, not spent", acc.get_currency(currency) == point)
    money = [(item, n) for item, n in reward if item in (DIAMOND, 100, 111)]
    check("reward credited", all(acc.get_currency(item) == before[item] + n for item, n in money) or not money,
          str({item: acc.get_currency(item) - before[item] for item, _ in money}))

    err, ids = await claim(c, rid)
    check("second claim: refused", err != 0 and not ids)
    err, ids = await claim(c, 123456)
    check("unknown reward: refused", err != 0 and not ids)

    m.save()
    again = missions.Missions(acc)
    check("kept in the account", rid in again.achievements_received())
    login = P.TaskChangeS2C()
    login.ParseFromString(again.change_message(all_tasks=True))
    check("sent again at login", rid in login.achievement_rewards_state)

    # another group is measured in its own currency
    other = sorted((r["Point"], i) for i, r in rows.items() if r["TaskTeamId"] == 8002 and i not in again.achievements_received())
    if other:
        p2, r2 = other[0]
        acc.set_currency(missions.ACHIEVEMENT_CURRENCY[8002], 0)
        acc.set_currency(currency, 10 ** 6)
        err, _ = await claim(c, r2)
        check("group 8002 ignores group 8001's points", err != 0)
        acc.set_currency(missions.ACHIEVEMENT_CURRENCY[8002], p2)
        err, ids = await claim(c, r2)
        check("group 8002 with its own points: accepted", err == 0 and ids == [r2])

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
