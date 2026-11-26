"""Offline test of the recruit wish list (0x0F0E LotteryWishListC2S) against the Garena capture: banner 150,
players 1105 / 1101 / 1103 -> 0x0F05 with the banner's LotteryUnit and wish_list [1105, 1101, 1103], then the
empty reply; the list stays after a new login; more than WishLimit (3) is refused. Temporary accounts.
  python test_lottery_wish.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-wish-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
CAPTURED_REQ = bytes.fromhex("0896011206d108cd08cf08")  # decoded_tshark.json, C->S 0x0F0E


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


async def main():
    c = await login(9931)
    c.sent.clear()
    await server.MESSAGE_HANDLERS[0x0F0E](c, CAPTURED_REQ)
    assert [m for m, _ in c.sent] == [0x0F05, 0x0F0E] and c.sent[1][1] == b""
    unit = P.LotteryUpdateS2C.FromString(c.sent[0][1]).lotterys[0]
    assert unit.id == 150 and list(unit.wish_list) == [1105, 1101, 1103]

    c2 = await login(9931)  # the list comes back with the banner's unit
    units = [u for m, p in c2.sent if m == 0x0F05 for u in P.LotteryUpdateS2C.FromString(p).lotterys if u.id == 150]
    assert units and list(units[-1].wish_list) == [1105, 1101, 1103]

    c2.sent.clear()  # four players: more than WishLimit 3
    await server.MESSAGE_HANDLERS[0x0F0E](c2, P.LotteryWishListC2S(id=150, conf_id_list=[1105, 1101, 1103, 1104])
                                          .SerializeToString())
    assert [m for m, _ in c2.sent] == [0x0F0E] and P.LotteryWishListS2C.FromString(c2.sent[0][1]).err == 1
    assert list(P.LotteryUnit.FromString(c2.account.lotteries[150]).wish_list) == [1105, 1101, 1103]
    print("OK", TMP)


asyncio.run(main())
