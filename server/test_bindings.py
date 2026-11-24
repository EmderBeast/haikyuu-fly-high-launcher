"""Offline test of the token binding (bindings.py): first login binds, other tokens are refused,
approve / release. Temporary folder, no port.
  python test_bindings.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

os.environ["HFH_BIND_TOKENS"] = "1"
import accounts  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="hfh-bind-"))
accounts.ACCOUNTS = TMP
import bindings  # noqa: E402
bindings.STORE = TMP / "bindings.json"
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import wire  # noqa: E402


class FakeClient:
    def __init__(self):
        self.sent, self.gid = [], 0

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid, token) -> bool:
    c = FakeClient()
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, token.encode())]))
    first = wire.parse(c.sent[0][1])
    ok = wire.get(first, 1, 0) == 0 and any(m == 0x0102 for m, _ in c.sent)
    return ok


async def main():
    assert await login(9801, "garena-token-A")            # first login binds
    assert await login(9801, "garena-token-A")            # same login again
    assert not await login(9801, "someone-else")          # another token: refused
    assert "9801" in bindings.pending()
    assert bindings.approve("9801")                       # the owner approves the new login
    assert await login(9801, "someone-else")
    assert not await login(9801, "garena-token-A")        # the old token is no longer bound
    assert bindings.release("9801")                       # released: next login binds anew
    assert await login(9801, "garena-token-C")
    raw = bindings.STORE.read_text(encoding="utf-8")
    assert "garena-token" not in raw                      # only hashes are stored
    os.environ["HFH_BIND_TOKENS"] = "0"                   # own server: no binding
    assert await login(9801, "anything")
    print("OK", TMP)


asyncio.run(main())
