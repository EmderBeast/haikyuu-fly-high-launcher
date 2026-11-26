"""Offline test of the mini games (minigame.py, 0x2B01 / 0x2B03): own state at login, a played game as in the
Garena capture garena4 (week score + points, best score kept, mission 6000006 = best single game, message
order 0x2B01, 0x0E02, 0x2B03), a better score, a game id that does not exist, state kept per player.
Temporary accounts, no port.
  python test_minigame.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-minigame-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import minigame  # noqa: E402
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

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def play(c, game, score):
    c.sent = []
    req = P.ArderResultC2S(game_id=game, score=score, ball_not_fall_id=1, ball_not_fall_catch=1)
    await server.MESSAGE_HANDLERS[0x2B03](c, req.SerializeToString())
    return c.sent


def task_progress(sent, tid):
    for mid, pb in sent:
        if mid == 0x0E02:
            for num, wt, v in wire.parse(pb):
                if num == 1 and wire.get(wire.parse(v), 1, 0) == tid:
                    return wire.get(wire.parse(v), 3, 0)
    return None


async def main():
    c = await login(990701)
    first = P.ArderInfoS2C.FromString(c.last(0x2B01))
    check("login: 0x2B01 with all = true and the week data", first.all and first.HasField("data"),
          f"week score {first.data.score}, games {[(g.id, g.max_score) for g in first.game_data]}")
    start = first.data.score
    best = {g.id: g.max_score for g in first.game_data}

    sent = await play(c, 1, 50)
    ids = [m for m, _ in sent]
    check("order as captured: 0x2B01, 0x0E02, 0x2B03", ids == [0x2B01, 0x0E02, 0x2B03], str([hex(i) for i in ids]))
    inf = P.ArderInfoS2C.FromString(sent[0][1])
    check("0x2B01 update without `all`, week score + 50", not inf.all and inf.data.score == start + 50,
          f"{start} -> {inf.data.score}")
    check("all games listed again", sorted(g.id for g in inf.game_data) == sorted(set(best) | {1}))
    res = P.ArderResultS2C.FromString(sent[-1][1])
    want = max(best.get(1, 0), 50)
    check("0x2B03 {game_id, score, max_score}", (res.err, res.game_id, res.score, res.max_score) == (0, 1, 50, want),
          str(res).replace("\n", " "))
    p1 = task_progress(sent, 6000006)
    check("mission 6000006 = best single game (50)", p1 == 50, str(p1))

    sent = await play(c, 1, 30)
    check("weaker game: mission unchanged, no TaskChange", task_progress(sent, 6000006) is None
          and [m for m, _ in sent] == [0x2B01, 0x2B03])
    check("weaker game: week score still adds up", P.ArderInfoS2C.FromString(sent[0][1]).data.score == start + 80)

    high = max(best.values(), default=0) + 1000
    sent = await play(c, 3, high)
    res = P.ArderResultS2C.FromString(sent[-1][1])
    check("a new best score becomes max_score", res.max_score == high)
    check("mission capped at its target and finished", task_progress(sent, 6000006) == 800)

    sent = await play(c, 2, 100)  # mini game 2 is not in minigame.json
    res = P.ArderResultS2C.FromString(sent[-1][1])
    check("unknown game: err, nothing booked", res.err != 0 and [m for m, _ in sent] == [0x2B03])

    c2 = await login(990701)
    again = P.ArderInfoS2C.FromString(c2.last(0x2B01))
    check("next login shows the player's own state", again.data.score == start + 80 + high
          and {g.id: g.max_score for g in again.game_data}.get(3) == high)
    other = P.ArderInfoS2C.FromString((await login(990702)).last(0x2B01))
    check("another player keeps the baseline", other.data.score == start)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
