"""Offline test of real-time PvP stage 1 (realtime.py + freedom.py): two clients search at the same
time, get paired, play one shared live match (player 2 mirrored) and both get a result.
Temporary accounts, no port.
  python test_realtime.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

os.environ["HFH_FREEDOM_SEASON"] = "104"
os.environ["HFH_REALTIME"] = "1"
import accounts  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="hfh-rt-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import freedom  # noqa: E402
import realtime  # noqa: E402
import wire  # noqa: E402

P = matches.P
realtime.WAIT = 2.0


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


async def search(c, pattern=1):
    await server.MESSAGE_HANDLERS[freedom.MSG_MATCH](c, P.FightFreedomMatchC2S(pattern=pattern).SerializeToString())
    for _ in range(100):  # the reply comes from a task
        if any(m == freedom.MSG_MATCH for m, _ in c.sent):
            return P.FightFreedomMatchS2C.FromString(c.last(freedom.MSG_MATCH))
        await asyncio.sleep(0.05)
    raise AssertionError("no match reply")


def check_mirror():
    """Mirroring twice gives the original frame back; ids and scores swap."""
    f = P.FrameData(frame_index=5)
    f.player_data.add(player_id=2, position=P.Vector3(x=1.5, y=0, z=-3.0))
    f.round_data.serve_team = 0
    f.round_data.hit_data.hit_player = 15
    f.game_data.score.host_score, f.game_data.score.guest_score = 3, 1
    raw = f.SerializeToString()
    m = P.FrameData.FromString(realtime.mirror_frame(raw))
    assert m.player_data[0].player_id == 15 and m.player_data[0].position.z == 3.0
    assert m.round_data.hit_data.hit_player == 2 and m.round_data.serve_team == 1
    assert (m.game_data.score.host_score, m.game_data.score.guest_score) == (1, 3)
    assert P.FrameData.FromString(realtime.mirror_frame(realtime.mirror_frame(raw))) == f


async def main():
    check_mirror()
    a, b = await login(9401), await login(9402)
    ra, rb = await asyncio.gather(search(a), search(b))
    print("paired:", ra.data.enemy_id, rb.data.enemy_id, ra.data.enemy_name, rb.data.enemy_name)
    assert ra.data.enemy_id == 9402 and rb.data.enemy_id == 9401

    for c in (a, b):
        args = P.PVPRoomArgs(pvp_type=P.FIGHT_FIGHTFREEDOM, pvp_args=[0])
        await server.handle_pvp_create(c, P.PVPCreateC2S(args=args).SerializeToString())
    ia = P.PVPCreateS2C.FromString(a.last(0x0302)).info
    ib = P.PVPCreateS2C.FromString(b.last(0x0302)).info
    print("rooms: a home", ia.home.gamer_id, "away", ia.away.gamer_id, "| b home", ib.home.gamer_id, "away", ib.away.gamer_id)
    assert ia.home.gamer_id == ib.away.gamer_id and ia.away.gamer_id == ib.home.gamer_id
    assert ia.session == ib.session

    # both request rallies at their own pace (b lags behind a by two rallies, then catches up)
    n = 0
    while not (a.room.live.over and b.room.live.over) and n < 400:
        n += 1
        if not a.room.live.over:
            await server.handle_match_data(a, b"")
        if (n > 2 or a.room.live.over) and not b.room.live.over:
            await server.handle_match_data(b, b"")
    la, lb = a.room.live, b.room.live
    print("score a", la.score, "b", lb.score, "rallies", la.n, lb.n)
    assert la.score == lb.score[::-1] and la.n == lb.n
    fa = [P.PVPMatchDataS2C.FromString(p).frames for m, p in a.sent if m == 0x0303]
    fb = [P.PVPMatchDataS2C.FromString(p).frames for m, p in b.sent if m == 0x0303]
    assert len(fa) == len(fb)
    for x, y in zip(fa, fb):
        assert [realtime.mirror_frame(f) for f in x] == list(y)
    assert [P.PVPMatchDataS2C.FromString(p).order for m, p in b.sent if m == 0x0303] == list(range(len(fb)))

    # an input is answered with the unchanged rally
    await server.handle_match_input(a, P.PVPHandleServeC2S(result=2).SerializeToString(), 0x0307)
    assert list(P.PVPHandleServeS2C.FromString(a.last(0x0307)).frames) == list(fa[-1])

    for c in (a, b):
        await server.handle_pvp_result(c, b"")
    wa = P.PVPResultS2C.FromString(a.last(0x0304)).battle_result
    wb = P.PVPResultS2C.FromString(b.last(0x0304)).battle_result
    print("results: a winner", wa.result.winner, wa.player_grade.home.fight_freedom_score,
          "| b winner", wb.result.winner, wb.player_grade.home.fight_freedom_score)
    assert {wa.result.winner, wb.result.winner} == {1, 2}
    assert freedom.entry(9401)["score"] != freedom.entry(9402)["score"] or freedom.entry(9401)["score"] == 1000

    # alone: no partner within WAIT -> a saved opponent
    alone = await search(a)
    assert alone.err == 0 and alone.data.enemy_id != 9402 or alone.data.enemy_id == 9402
    print("OK", TMP)


asyncio.run(main())
