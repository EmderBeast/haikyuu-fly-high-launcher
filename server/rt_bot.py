"""
Test client for real-time PvP: logs in over TCP like the game, searches a Freedom Championship
match and plays it (requests rallies at roughly the game's pace), so a single emulator can be
tested against a second "player".

  python rt_bot.py --port 10105 --gid 9501 [--pattern 1] [--delay 4]
"""
import argparse
import asyncio
import gzip
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "proto_gen"))
import haikyuu_pb2 as P  # noqa: E402

LCG_A, LCG_B, LCG_MOD = 214003, 2531012, 2 ** 32


def lcg_next(seed: int):
    new = (seed * LCG_A + LCG_B) % LCG_MOD
    return new, struct.pack("<I", new)


def bcc(data: bytes) -> int:
    v = 0
    for b in data:
        v ^= b
    return v


def encrypt(data: bytes, key: bytes) -> bytes:
    out, prev = bytearray(len(data)), 0
    for i, b in enumerate(data):
        out[i] = ((b ^ key[i % 4]) + prev) & 0xFF
        prev = out[i]
    return bytes(out)


def decrypt(data: bytes, key: bytes) -> bytes:
    out, prev = bytearray(len(data)), 0
    for i, b in enumerate(data):
        out[i] = key[i % 4] ^ ((b - prev) & 0xFF)
        prev = b
    return bytes(out)


class Bot:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.c2s = self.s2c = 0
        self.inbox: dict[int, list[bytes]] = {}

    async def read_packet(self):
        head = await self.r.readexactly(8)
        size = struct.unpack_from("<I", head)[0]
        mid, flags = (head[4] << 8) | head[5], head[6]
        body = await self.r.readexactly(size)
        return mid, flags, body

    async def start(self):
        mid, flags, body = await self.read_packet()  # seed
        self.c2s, self.s2c = struct.unpack(">I", body[1:5])[0], struct.unpack(">I", body[5:9])[0]
        asyncio.create_task(self._reader())

    async def _reader(self):
        try:
            while True:
                mid, flags, body = await self.read_packet()
                if flags & 1:
                    self.s2c, key = lcg_next(self.s2c)
                    body = decrypt(body, key)
                if flags & 2:
                    body = gzip.decompress(body)
                self.inbox.setdefault(mid, []).append(body[1:] if body[:1] == b"\x00" else body)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def send(self, mid: int, pb: bytes):
        body = b"\x00" + pb
        self.c2s, key = lcg_next(self.c2s)
        enc = encrypt(body, key)
        self.w.write(struct.pack("<I", len(enc)) + bytes([mid >> 8, mid & 0xFF, 1, bcc(body)]) + enc)
        await self.w.drain()

    async def wait(self, mid: int, timeout=30.0) -> bytes:
        end = time.time() + timeout
        while time.time() < end:
            if self.inbox.get(mid):
                return self.inbox[mid].pop(0)
            await asyncio.sleep(0.05)
        raise TimeoutError(f"no 0x{mid:04X}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10105)
    ap.add_argument("--gid", type=int, default=9501)
    ap.add_argument("--pattern", type=int, default=1)
    ap.add_argument("--delay", type=float, default=4.0, help="seconds per rally (watching time)")
    ap.add_argument("--searches", type=int, default=20, help="how often to search before giving up")
    ap.add_argument("--want", type=int, default=0, help="only accept this opponent gid (the real-time partner)")
    a = ap.parse_args()
    reader, writer = await asyncio.open_connection("127.0.0.1", a.port)
    bot = Bot(reader, writer)
    await bot.start()
    pb = bytes([0x08]) + _varint(a.gid) + bytes([0x3A, 5]) + b"token"  # 1=gid, 7=token
    await bot.send(0x0101, pb)
    await bot.wait(0x0102, 60)
    print(f"[bot {a.gid}] logged in", flush=True)
    for n in range(a.searches):
        await bot.send(0x520A, P.FightFreedomMatchC2S(pattern=a.pattern).SerializeToString())
        m = P.FightFreedomMatchS2C.FromString(await bot.wait(0x520A, 30))
        print(f"[bot {a.gid}] search {n + 1}: err {m.err} opponent {m.data.enemy_id} {m.data.enemy_name}", flush=True)
        if m.err == 0 and m.data.enemy_id > 0 and (not a.want or m.data.enemy_id == a.want):
            break
        if m.err == 0:
            await bot.send(0x0305, P.PVPCloseC2S().SerializeToString())  # not a real player: give up, search again
            await bot.wait(0x0305, 10)
    else:
        print(f"[bot {a.gid}] no real opponent found", flush=True)
        return
    await bot.send(0x0302, P.PVPCreateC2S(args=P.PVPRoomArgs(pvp_type=18, pvp_args=[0])).SerializeToString())
    room = P.PVPCreateS2C.FromString(await bot.wait(0x0302)).info
    print(f"[bot {a.gid}] room: home {room.home.name} ({room.home.gamer_id}) vs away {room.away.name} "
          f"({room.away.gamer_id})", flush=True)
    order = -1
    while True:
        await bot.send(0x0303, P.PVPMatchDataC2S(order=order).SerializeToString())
        md = P.PVPMatchDataS2C.FromString(await bot.wait(0x0303, 60))
        order = md.order
        end = [f for f in md.frames if P.FrameData.FromString(f).HasField("game_data")]
        score = P.FrameData.FromString(end[-1]).game_data.score if end else None
        over = any(P.FrameData.FromString(f).HasField("match_data") and P.FrameData.FromString(f).match_data.end
                   for f in md.frames)
        print(f"[bot {a.gid}] rally order {md.order}: {len(md.frames)} frames, score "
              f"{score.host_score if score else '?'}-{score.guest_score if score else '?'}", flush=True)
        if over or (score and max(score.host_score, score.guest_score) >= room.bo_score):
            break
        await asyncio.sleep(a.delay)
    await bot.send(0x0304, P.PVPResultC2S(session=room.session).SerializeToString())
    res = P.PVPResultS2C.FromString(await bot.wait(0x0304)).battle_result
    print(f"[bot {a.gid}] result: winner {res.result.winner}, freedom score "
          f"{res.player_grade.home.fight_freedom_score.old_score}->{res.player_grade.home.fight_freedom_score.new_score}",
          flush=True)
    writer.close()


def _varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


if __name__ == "__main__":
    asyncio.run(main())
