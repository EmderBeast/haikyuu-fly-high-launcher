"""Offline test of the creative mode (admin.creative): on an own server a new account comes in at level
max with every character and infinite stamina, later logins only top currencies/items up; on a shared
(multiplayer) server nothing happens. Temporary accounts.
  python test_creative.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-creative-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG, admin.ACCOUNTS, admin.MULTIPLAYER = TMP / "admin.json", TMP, TMP / "multiplayer.json"
admin.QUEUE, admin.DONE = TMP / "admin_queue", TMP / "admin_done"
admin.ALL_QUEUE, admin.UNLOCKED, admin.LAST_LOGIN = admin.QUEUE / "all.json", TMP / "unl.json", TMP / "last.json"
import gameconfig  # noqa: E402
import wire  # noqa: E402


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
    admin.LIVE = True  # as the running server
    os.environ.pop("HFH_MULTIPLAYER", None)
    c = await login(920001)
    acc = c.account
    assert acc.level == gameconfig.MAX_PLAYER_LEVEL, acc.level
    assert acc.admin_flags.get(admin._creative_key()) == admin.CREATIVE_VERSION and acc.admin_flags.get("infinite_stamina")
    assert len(acc.owned_conf_ids()) > 100, len(acc.owned_conf_ids())
    assert admin.creative(acc) == admin.CREATIVE_TOPUP
    os.environ["HFH_MULTIPLAYER"] = "1"
    m = await login(920002)
    assert m.account.level < gameconfig.MAX_PLAYER_LEVEL and not m.account.admin_flags.get(admin._creative_key())
    print("test_creative: OK")


asyncio.run(main())
