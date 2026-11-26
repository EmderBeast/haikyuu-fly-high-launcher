"""Offline test of the hand serve side (0x0307 PVPHandleServeC2S.s_index, EHandServeArea 76 left /
77 mid / 78 right): the serve is received on that side of the other half, as in the Garena captures
(left x about -3, mid about 0, right about +3). Temporary accounts, no port.
  python test_hand_serve_area.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-serve-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
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

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


def receive(frames):
    """(player, x, z) of the first receive of a serve in these frames, (server, None, None) without one."""
    server_id = None
    for raw in frames:
        f = P.FrameData()
        f.ParseFromString(raw)
        h = f.round_data.hit_data
        if not f.round_data.HasField("hit_data"):
            continue
        if h.formula_type == 1:
            server_id = h.hit_player
        elif h.formula_type == 2:
            if not h.HasField("hit_ball_pos"):  # a missed receive (ace) carries no contact point
                return h.hit_player, None, None
            return h.hit_player, h.hit_ball_pos.x, h.hit_ball_pos.z
    return server_id, None, None


async def main():
    c = FakeClient(990501)
    await server.handle_login(c, wire.build([(1, 0, 990501), (7, 2, b"token")]))
    want = {76: lambda x: x < -1.5, 77: lambda x: -1.5 <= x <= 1.5, 78: lambda x: x > 1.5}
    seen = {76: [], 77: [], 78: []}
    serves = 0
    for match_no in range(6):
        req = P.PVPCreateC2S(args=P.PVPRoomArgs(pvp_type=P.FIGHT_SPECIFIC_TRIAL, pvp_args=[0, 320101]))
        await server.handle_pvp_create(c, req.SerializeToString())
        live = c.room.live
        for _ in range(40):
            if live.over:
                break
            await server.handle_match_data(c, P.PVPMatchDataC2S(session=live.info.session, order=live.order).SerializeToString())
            first = P.PVPMatchDataS2C()
            first.ParseFromString(c.last(0x0303))
            f1 = [P.FrameData.FromString(f) for f in first.frames]
            home_serves = any(f.round_data.HasField("hit_data") and f.round_data.hit_data.formula_type == 1
                              and f.round_data.hit_data.hit_player < 13 for f in f1)
            if not home_serves:
                continue
            serves += 1
            for area in (76, 77, 78):
                body = P.PVPHandleServeC2S(s_index=area, result=2).SerializeToString()
                await server.handle_match_input(c, body, 0x0307)
                rep = P.PVPHandleServeS2C()
                rep.ParseFromString(c.last(0x0307))
                player, x, z = receive(rep.frames)
                if x is not None:
                    seen[area].append((player, round(x, 2), round(z, 2)))
        if serves >= 6:
            break
    check("home served often enough", serves >= 3, f"{serves} serves")
    for area, name in ((76, "left"), (77, "mid"), (78, "right")):
        xs = [x for _, x, _ in seen[area]]
        check(f"{name} ({area}): received on that side", bool(xs) and all(want[area](x) for x in xs),
              f"{len(xs)} receives, x {min(xs, default=0)}..{max(xs, default=0)}")
        check(f"{name}: on the other team's half", all(p >= 13 and z > 0 for p, _, z in seen[area]))
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
