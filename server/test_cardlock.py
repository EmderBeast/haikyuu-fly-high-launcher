"""Offline test of the card lock (cardlock.py): 0x0902 with the whole card and `locked` as requested (no opt
field, as captured), then the empty 0x0909 reply; the lock stays after a new login; unknown card -> error.
Temporary accounts.
  python test_cardlock.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-cardlock-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import cardlock  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P


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


def card_of(acc, inst):
    return P.PlayerCard.FromString(acc.get_card(inst))


async def main():
    c = await login(9891)
    acc = c.account
    inst = next(iter(acc.b.cards))
    for lock in (True, False, True):
        c.sent.clear()
        await server.MESSAGE_HANDLERS[cardlock.MSG_LOCK](c, P.PlayerCardLockC2S(inst_id=inst, lock=lock).SerializeToString())
        assert [m for m, _ in c.sent] == [cardlock.MSG_CARD_CHANGE, cardlock.MSG_LOCK]
        change = wire.parse(c.sent[0][1])
        assert [n for n, _, _ in change] == [1]  # only the card, no opt field (as captured)
        sent = P.PlayerCard.FromString(wire.get(wire.parse(wire.get(change, 1, b"")), 1, b""))
        assert sent.inst_id == inst and sent.locked == lock and card_of(acc, inst).locked == lock
        assert c.sent[1][1] == b""
    print("locked:", card_of(acc, inst).locked)

    c2 = await login(9891)  # after a new login the card is still locked
    assert card_of(c2.account, inst).locked

    c2.sent.clear()
    await server.MESSAGE_HANDLERS[cardlock.MSG_LOCK](c2, P.PlayerCardLockC2S(inst_id=12345, lock=True).SerializeToString())
    assert [m for m, _ in c2.sent] == [cardlock.MSG_LOCK] and P.PlayerCardLockS2C.FromString(c2.sent[0][1]).error == 1
    print("OK", TMP)


asyncio.run(main())
