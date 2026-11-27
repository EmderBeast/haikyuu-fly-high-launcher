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

    # week rewards (0x2B02): a player with nothing claimed yet and 1000 week points
    c3 = await login(990703)
    d = minigame.state(990703)
    d["week"].update(score=1000, received=0)
    rows = minigame._rows()

    async def claim(row_id):
        c3.sent = []
        await server.MESSAGE_HANDLERS[0x2B02](c3, P.ArderRewardC2S(id=row_id).SerializeToString())
        return c3.sent, P.ArderRewardS2C.FromString(c3.sent[-1][1])

    acc = c3.account
    before = (acc.get_currency(100), acc.item_count(201))
    sent, rep = await claim(1)
    ids = [m for m, _ in sent]
    check("claim row 1 (400 points): ok, 0x2B01 then 0x2B02 last", rep.err == 0 and ids[-2:] == [0x2B01, 0x2B02],
          str([hex(i) for i in ids]))
    inf = P.ArderInfoS2C.FromString(sent[-2][1])
    check("received gets bit 1 << Group", inf.data.received == 1 << rows[1]["Group"], str(inf.data.received))
    check("no own reward popup (the client shows it from the table)", 0x0804 not in ids and 0x0805 not in ids)
    after = (acc.get_currency(100), acc.item_count(201))
    check("gold +5000 and item 201 +1 as in minigamereward row 1", (after[0] - before[0], after[1] - before[1]) == (5000, 1),
          f"{before} -> {after}")
    _, rep = await claim(1)
    check("claimed twice: refused", rep.err != 0)
    sent, rep = await claim(3)  # 1800 points needed
    check("score too low: refused, nothing sent but the reply", rep.err != 0 and len(sent) == 1)
    _, rep = await claim(99)
    check("unknown row: refused", rep.err != 0)
    _, rep = await claim(2)
    check("row 2 (900 points) then ok, received = groups 1 and 2", rep.err == 0
          and minigame.state(990703)["week"]["received"] == (1 << 1) | (1 << 2))

    # assistant function 1, as in capture decrypted_1 (week score 4200, nothing claimed before)
    c4 = await login(990704)
    minigame.state(990704)["week"].update(score=4200, received=0)
    garena_1 = [(102, 15000), (100, 10000), (201, 1), (102, 20000), (100, 15000), (201, 2), (102, 25000),
                (100, 20000), (201, 2), (108, 150), (100, 25000), (201, 3), (102, 10000), (100, 5000), (201, 1)]
    c4.sent = []
    await server.handle_share_assist_do(c4, b"")
    units = {}
    for num, wt, v in wire.parse(c4.sent[-1][1]):
        if num == 2:
            u = wire.parse(v)
            units[wire.get(u, 1)] = [(wire.get(wire.parse(i), 2), wire.get(wire.parse(i), 3)) for n, _, i in u if n == 3]
    check("assistant unit 1 = Garena's 15 items (each row's Reward, not summed)",
          sorted(units.get(1, [])) == sorted(garena_1), str(units.get(1)))
    ids = [m for m, _ in c4.sent]
    first = ids.index(0x2B01) if 0x2B01 in ids else -1
    check("0x2B01 (received 62) before the item changes, as captured",
          first >= 0 and P.ArderInfoS2C.FromString(c4.sent[first][1]).data.received == 62
          and all(m not in (0x0802, 0x0405) for m in ids[:first]))
    c4.sent = []
    await server.handle_share_assist_do(c4, b"")
    check("all claimed: function 1 has nothing more to do", 1 not in {wire.get(wire.parse(v), 1) for n, _, v
                                                                         in wire.parse(c4.sent[-1][1]) if n == 2})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
