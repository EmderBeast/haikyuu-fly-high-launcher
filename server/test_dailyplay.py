"""Offline test of the daily events (0x2D01 / 0x2D02, assistant function 2) against the own recording wcy2711
(27.11.2026, login 17:38 UTC) and decrypted_1. Temporary accounts, no port.
  python test_dailyplay.py
"""
import asyncio
import random
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-daily-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import dailyplay as D  # noqa: E402
import wire  # noqa: E402

P = server.matches.P
LOGIN = 1795801080  # 27.11.2026 17:38 UTC
checks = []


class Clock:
    now = LOGIN

    @classmethod
    def time(cls):
        return cls.now


D.time = Clock


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


async def main():
    # season_time of every type at the recording's login, as Garena sent it
    want = {1: 1795759200, 2: 1795791600, 3: 1795773600, 4: 1795773600}
    got = {t: D.last_slot(t, LOGIN) for t in want}
    check("season_time = the last RefreshTime hour (UTC), as in wcy2711", got == want, f"{got}")

    c = FakeClient(990901)
    await server.handle_login(c, wire.build([(1, 0, 990901), (7, 2, b"token")]))
    login = [P.ShopEventInfoS2C.FromString(p) for m, p in c.sent if m == D.MSG_INFO]
    info = {u.event_type: u for u in login[-1].events} if login else {}
    check("login sends this player's 0x2D01 (all)", login and login[-1].all and sorted(info) == [1, 2, 3, 4])
    check("plays left 2 / 2 / 1 / 2 and one waiting event for types 1, 2, 4 (none for 3), as in wcy2711",
          [info[t].current_count for t in (1, 2, 3, 4)] == [2, 2, 1, 2]
          and [len(info[t].event) for t in (1, 2, 3, 4)] == [1, 1, 0, 1]
          and all(info[t].season_time == want[t] for t in want), str(login[-1]).replace("\n", " ")[:300])

    # the assistant: every play left of types 1, 2, 4, each WinReward on its own, then the deletions
    d = D.state(990901)
    waiting = {t: list(d["types"][str(t)]["events"]) for t in (1, 2, 4)}
    c.sent.clear()
    shown = await D.assistant(c, rng=random.Random(3))
    check("2 events of each of types 1, 2, 4 -> six rewards, not summed", len(shown) == 6, f"{shown}")
    check("type 4 = event 401001's WinReward twice (131 x20), as in wcy2711", shown[-2:] == [(131, 20), (131, 20)])
    upd = P.ShopEventInfoS2C.FromString(c.sent[-1][1])
    check("then 0x2D01: the waiting events deleted, no plays left",
          c.sent[-1][0] == D.MSG_INFO and not upd.all
          and {u.event_type: [e.event_id for e in u.event if e.deleted] for u in upd.events} == waiting
          and all(not u.current_count for u in upd.events), str(upd).replace("\n", " ")[:300])
    check("done for the assistant (types 1, 2, 4 at 0)", all(d["types"][str(t)]["left"] == 0 for t in (1, 2, 4)))
    c.sent.clear()
    check("nothing left: no unit", await D.assistant(c) == [] and not c.sent)

    # playing one by hand (0x2D02): next day, after the first slots
    Clock.now = LOGIN + 86400 - 8 * 3600  # 28.11. 09:38 UTC: types 1 (06:00), 2 (09:00) waiting
    d = D.state(990901)
    ev = d["types"]["2"]["events"][0]
    row = D.EVENTS[ev]
    c.sent.clear()
    gold = c.account.get_currency(D.reward(row, "WinReward")[0][0])
    await D.handle_finish(c, P.ShopEventFinishC2S(event_id=ev, is_win=1, season_time=d["types"]["2"]["season"]).SerializeToString())
    reply = P.ShopEventFinishS2C.FromString(c.sent[-1][1])
    upd = P.ShopEventInfoS2C.FromString([p for m, p in c.sent if m == D.MSG_INFO][-1])
    item, n = D.reward(row, "WinReward")[0]
    check("new day: plays again", d["types"]["1"]["left"] == 2 and len(d["types"]["1"]["events"]) == 1)
    check("won: WinReward credited, event deleted, one play left, reply without error",
          reply.err == 0 and c.account.get_currency(item) - gold == n
          and upd.events[0].current_count == 1 and upd.events[0].event[0].deleted)
    c.sent.clear()
    await D.handle_finish(c, P.ShopEventFinishC2S(event_id=ev, is_win=1).SerializeToString())
    check("the same event again: FAILED", P.ShopEventFinishS2C.FromString(c.sent[-1][1]).err == D.FAILED)
    Clock.now += 6 * 3600  # 15:00 slot: a new type 2 event appears (one play left)
    d = D.state(990901)
    check("15:00 UTC: a new type-2 event appears", len(d["types"]["2"]["events"]) == 1)
    ev2 = d["types"]["2"]["events"][0]
    lost = D.reward(D.EVENTS[ev2], "DefeatReward") or D.reward(D.EVENTS[ev2], "WinReward")
    before = c.account.get_currency(lost[0][0])
    await D.handle_finish(c, P.ShopEventFinishC2S(event_id=ev2, is_win=0).SerializeToString())
    check("lost: DefeatReward", c.account.get_currency(lost[0][0]) - before == lost[0][1])
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
