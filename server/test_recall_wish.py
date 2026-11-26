"""Offline test of the memory recruit wish (recallwish.py, 0x0F0C) against the Garena capture: pool 27, item
506001023 -> 0x0F0A with only that pool's RecallLotteryUnit (byte for byte the captured reply when the account
is the captured one), then the empty 0x0F0C; the wish comes back in the login's 0x0F0A; pools without
WishMenu and items that are no memory are refused. Temporary accounts.
  python test_recall_wish.py
"""
import asyncio
import json
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-recallwish-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import recallwish  # noqa: E402
import wire  # noqa: E402

P = matches.P
REPLY = bytes.fromhex(json.loads((Path(__file__).parent / "data" / "replies.json").read_text())["0x0F0A"]["body_hex"])[1:]


class FakeClient:
    def __init__(self, gid, acc=None):
        self.gid, self.sent, self.writer, self.account = gid, [], None, acc

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def main():
    # the captured account: the reply is the captured one, byte for byte
    cap = accounts.Account(9941, accounts.CAPTURED)
    c = FakeClient(9941, cap)
    await server.MESSAGE_HANDLERS[0x0F0C](c, P.LotteryRecallWishC2S(id=27, item_id=506001023).SerializeToString())
    assert [m for m, _ in c.sent] == [0x0F0A, 0x0F0C] and c.sent[1][1] == b""
    assert c.sent[0][1] == REPLY, (c.sent[0][1].hex(), REPLY.hex())

    # a login sends the pool with the new wish, all other pools as before
    c = FakeClient(9942)
    await server.handle_login(c, wire.build([(1, 0, 9942), (7, 2, b"token")]))
    await server.MESSAGE_HANDLERS[0x0F0C](c, P.LotteryRecallWishC2S(id=27, item_id=506001023).SerializeToString())
    c2 = FakeClient(9942)
    await server.handle_login(c2, wire.build([(1, 0, 9942), (7, 2, b"token")]))
    info = P.LotteryRecallInfoS2C.FromString(next(p for m, p in c2.sent if m == 0x0F0A))
    base = P.LotteryRecallInfoS2C.FromString(c2.account.b.pb("0x0F0A"))
    got = {u.pool_id: u.wish for u in info.lotterys}
    assert got[27] == 506001023
    assert len(info.lotterys) == len(base.lotterys) + (0 if 27 in {u.pool_id for u in base.lotterys} else 1)
    assert all(got[u.pool_id] == u.wish for u in base.lotterys if u.pool_id != 27)

    # refused: a pool without WishMenu, an item that is no memory
    no_menu = next(i for i, r in recallwish.POOLS.items() if not r.get("WishMenu"))
    for req in (P.LotteryRecallWishC2S(id=no_menu, item_id=506001023), P.LotteryRecallWishC2S(id=27, item_id=108)):
        c2.sent.clear()
        await server.MESSAGE_HANDLERS[0x0F0C](c2, req.SerializeToString())
        assert [m for m, _ in c2.sent] == [0x0F0C] and P.LotteryRecallWishS2C.FromString(c2.sent[0][1]).err == 1
    print("OK", TMP)


asyncio.run(main())
