"""Offline test of Cumulative Online Rewards (onlinereward.py, local activity type 3): own state at login,
claiming the reached stages with gear -1 as the client does, the Garena claim of stages 4-6
(decoded_tshark.json 2805-2809: update, item/currency changes, popup 102 x15000 / 100 x25000 / 102000014 x1,
reply), nothing left -> refused, logout books the session, the daily reset at 06:00 UTC. Temporary accounts.
  python test_online_reward.py
"""
import asyncio
import shutil
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-online-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import onlinereward as O  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def type3(payload, cls=P.LocalActivityInfoS2C):
    for a in cls.FromString(payload).activity_list:
        if a.type == 3:
            return P.ActivityOnlineRewardData.FromString(a.data)
    return None


async def claim(c, gear=-1):
    c.sent = []
    req = P.LocalActivityRewardC2S(type=3, data=P.ActivityOnlineRewardReward(gear=gear).SerializeToString())
    await server.MESSAGE_HANDLERS[0x2803](c, req.SerializeToString())
    return c.sent


def popup(sent):
    for mid, pb in sent:
        if mid == 0x0404:
            return sorted((wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0))
                          for n, _, v in wire.parse(pb) if n == 1)
    return None


async def main():
    now = int(time.time())
    c = await login(990801)
    info = next(pb for mid, pb in c.sent if mid == 0x2801)
    d0 = type3(info)
    check("login: own type-3 data, session starts now, nothing claimed",
          d0 is not None and abs(d0.online_start_sec - now) <= 5 and d0.history_online_sec == 0
          and list(d0.gear_reward_got) == [] and d0.last_reset_sec >= O.day_start(now), str(d0).replace("\n", " "))
    check("the other activities of the login are kept",
          {a.type for a in P.LocalActivityInfoS2C.FromString(info).activity_list} >= {1, 3, 8, 11})

    sent = await claim(c)
    check("right after login nothing is reached: refused, no update", [m for m, _ in sent] == [0x2803]
          and P.LocalActivityRewardS2C.FromString(sent[0][1]).err != 0)

    d = O.state(990801)
    d["start"] = now - 700  # 11 min 40 s online: stages 1-3 (1 / 5 / 10 min)
    sent = await claim(c)
    ids = [m for m, _ in sent]
    check("claim: 0x2802 first, popup 0x0404, reply 0x2803 last", ids[0] == 0x2802 and 0x0404 in ids and ids[-1] == 0x2803,
          str([hex(i) for i in ids]))
    upd = type3(sent[0][1], P.LocalActivityUpdateS2C)
    check("stages 1-3 claimed", list(upd.gear_reward_got) == [1, 2, 3], str(list(upd.gear_reward_got)))
    rep = P.LocalActivityRewardS2C.FromString(sent[-1][1])
    check("reply {type 3} without error", rep.type == 3 and rep.err == 0)
    check("rewards of stages 1-3 (onlinerewardsconf)", popup(sent) == sorted([(102, 5000), (507001002, 2), (115, 10)]),
          str(popup(sent)))

    d["start"] = now - 3000  # 50 min: stages 4-6, as in the Garena claim
    sent = await claim(c)
    check("Garena's claim of stages 4-6: popup 102 x15000, 100 x25000, 102000014 x1",
          popup(sent) == sorted([(102, 15000), (100, 25000), (102000014, 1)]), str(popup(sent)))
    check("all six stages claimed", sorted(type3(sent[0][1], P.LocalActivityUpdateS2C).gear_reward_got) == [1, 2, 3, 4, 5, 6])
    sent = await claim(c)
    check("nothing left: refused", P.LocalActivityRewardS2C.FromString(sent[-1][1]).err != 0)

    O.logout(990801, now + 100)
    check("logout books the session", O.state(990801)["history"] == 3100 and O.state(990801)["start"] == 0)
    c = await login(990801)
    d1 = type3(next(pb for mid, pb in c.sent if mid == 0x2801))
    check("same day: history and stages stay", d1.history_online_sec == 3100 and sorted(d1.gear_reward_got) == [1, 2, 3, 4, 5, 6])

    d = O.state(990801)
    d["reset"] -= 86400  # last reset yesterday
    c = await login(990801)
    d2 = type3(next(pb for mid, pb in c.sent if mid == 0x2801))
    check("new day: history 0, no stages, reset = start = login", d2.history_online_sec == 0
          and list(d2.gear_reward_got) == [] and d2.last_reset_sec == d2.online_start_sec)

    # a session that runs over 06:00 UTC counts from 06:00
    six = O.day_start(now)
    d.update(start=six - 1000, reset=six - 86400 + 10, history=500, gears=[1, 2, 3])
    O.claim(d, -1, six + 400)
    check("session over 06:00: restarts at 06:00 with no stages", d["reset"] == six and d["start"] == six
          and d["history"] == 0 and d["gears"] == [1, 2], str(d))
    check("day start is 06:00 UTC", six % 86400 == 6 * 3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
