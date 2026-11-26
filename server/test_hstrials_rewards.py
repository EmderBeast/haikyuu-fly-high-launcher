"""Offline test of the High School Trials' free daily reward (0x5602) and stage rewards (0x5604) as the client's
code and the tables describe them, on the captured account (all taken) with the state turned back. Temporary
accounts.
  python test_hstrials_rewards.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-hstrials2-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import coach  # noqa: E402
import hstrials  # noqa: E402
import matches  # noqa: E402
from missions import Missions  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid, acc):
        self.gid, self.sent, self.writer, self.account = gid, [], None, acc
        if acc is not None:
            self.missions = Missions(acc)

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def call(c, msg_id, pb=b""):
    start = len(c.sent)
    await server.MESSAGE_HANDLERS[msg_id](c, pb)
    sent = c.sent[start:]
    reply = sent[-1][1]
    err = P.HighSchoolTrialsDailyRewardsS2C.FromString(reply).err if reply else 0
    return err, sent


def base_in(sent):
    return P.HighSchoolTrialsInfoS2C.FromString(next(p for m, p in sent if m == hstrials.MSG_INFO)).base


async def main():
    acc = accounts.Account(9971, accounts.CAPTURED)
    c = FakeClient(9971, acc)
    gid = 9971
    check("handlers registered", all(server.MESSAGE_HANDLERS.get(m) for m in (0x5602, 0x5604)))
    check("capture: 780 stages passed, every stage reward and today's free reward taken",
          hstrials.total_stages(acc) == 780 and hstrials.base(gid, acc).cumulative_reward_id == 780 and hstrials.base(gid, acc).daily_reward_state)
    check("table: 56 stage rewards up to 780, each with content", len(hstrials.STAGE_REWARDS) == 56 and max(r["AwardConditions"] for r in hstrials.STAGE_REWARDS.values()) == 780
          and all(hstrials.stage_rewards(r) for r in hstrials.STAGE_REWARDS.values()))

    err, sent = await call(c, hstrials.MSG_DAILY)
    check("free reward already taken: error, nothing else sent", err == 1 and len(sent) == 1)
    err, sent = await call(c, hstrials.MSG_TOTAL)
    check("no stage reward left: error, nothing else sent", err == 1 and len(sent) == 1)

    # the daily reset gives the free reward back
    st = hstrials._state(gid, acc)
    st["reset"] = 1
    b = hstrials.base(gid, acc)
    check("after the daily reset the free reward is open, buys at 0", not b.daily_reward_state and b.daily_reward_buy_num == 0 and b.daily_reset_next_unix > 1)
    gold, dia, res = acc.get_currency(100), acc.get_currency(108), acc.get_currency(336)
    coaches = len(coach.cards(gid, acc))
    err, sent = await call(c, hstrials.MSG_DAILY)
    order = [m for m, _ in sent]
    check("free reward: accepted, base first, pop-up before the reply", err == 0 and order[0] == hstrials.MSG_INFO and 0x0404 in order and order[-1] == hstrials.MSG_DAILY, str([hex(m) for m in order]))
    check("free reward: base says taken", base_in(sent).daily_reward_state)
    check("free reward: daily row 17 without a price (gold +245000, diamonds unchanged)", acc.get_currency(100) - gold == 245000 and acc.get_currency(108) == dia)
    kept = len(coach.cards(gid, acc)) - coaches
    check("free reward: the coach boxes are opened (2 orange coaches kept, the rest dismantled)", kept == 2 and acc.get_currency(336) - res == 49000 + 3840, f"{kept} kept, 336 +{acc.get_currency(336) - res}")
    err, sent = await call(c, hstrials.MSG_DAILY)
    check("free reward a second time: error", err == 1 and len(sent) == 1)
    check("sent again at login as taken", P.HighSchoolTrialsInfoS2C.FromString(hstrials.info(gid, acc)).base.daily_reward_state)

    # stage rewards: turn the claimed mark back to 740 -> rows 750 and 780 are open
    hstrials._state(gid, acc)["cum"] = 740
    rows = [r for r in hstrials.STAGE_REWARDS.values() if 740 < r["AwardConditions"] <= 780]
    want = {}
    for r in rows:
        for item, n in hstrials.stage_rewards(r):
            want[item] = want.get(item, 0) + n
    before = {i: (acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i)) for i in want}
    err, sent = await call(c, hstrials.MSG_TOTAL)
    after = {i: (acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i)) for i in want}
    check("stage rewards: both open rows taken in one request", err == 0 and len(rows) == 2 and base_in(sent).cumulative_reward_id == 780, f"{len(rows)} rows")
    check("stage rewards: credited as in the table", all(after[i] - before[i] == n for i, n in want.items()), str({i: after[i] - before[i] for i in want}))
    check("stage rewards: base first, reply last", sent[0][0] == hstrials.MSG_INFO and sent[-1][0] == hstrials.MSG_TOTAL)
    err, sent = await call(c, hstrials.MSG_TOTAL)
    check("stage rewards again: error", err == 1)

    # a new account still gets the captured trial progress (the stages are not built): everything is taken there too
    d = FakeClient(990751, None)
    import wire
    await server.handle_login(d, wire.build([(1, 0, 990751), (7, 2, b"token")]))
    e1, _ = await call(d, hstrials.MSG_DAILY)
    e2, _ = await call(d, hstrials.MSG_TOTAL)
    check("new account: nothing to take twice either", e1 == 1 and e2 == 1)
    check("another account is not affected", "990751" not in pvp.state()["hstrials"] or pvp.state()["hstrials"]["990751"].get("cum") is None)

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
