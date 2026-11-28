"""Upgrading a room (0x2306): new account, lobby Lv.1 -> 2 for 200 x 227, kept over a new login."""
import asyncio, os, tempfile
from pathlib import Path
import accounts
TMP = Path(tempfile.mkdtemp()); accounts.ACCOUNTS = TMP
import pvp; pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
os.environ["HFH_MULTIPLAYER"] = "1"
import server, wire, homestead
P = homestead.P


class C:
    def __init__(s, gid): s.gid, s.sent, s.writer = gid, [], None
    async def send_packet(s, mid, body=b"", **k): s.sent.append((mid, body))
    async def send_raw(s, mid, fl, body): s.sent.append((mid, body))


def login(gid):
    c = C(gid)
    asyncio.run(server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"t")])))
    return c


c = login(940001)
info = P.HomeInfoS2C.FromString(next(b for m, b in c.sent if m == 0x2301))
assert sorted(a.level_id for a in info.areas_in) == [1010101, 1010401], info.areas_in
have = c.account.get_currency(227)
assert have >= 200, have
c.sent.clear()
asyncio.run(server.MESSAGE_HANDLERS[0x2306](c, P.HomeIndoorAreaLevelUpC2S(area_id=10101).SerializeToString()))
r = P.HomeIndoorAreaLevelUpS2C.FromString(next(b for m, b in c.sent if m == 0x2306))
assert r.err == 0 and r.level_id == 1010102 and r.comfort.total == 70, r
assert c.account.get_currency(227) == have - 200
c.sent.clear()
asyncio.run(server.MESSAGE_HANDLERS[0x2306](c, P.HomeIndoorAreaLevelUpC2S(area_id=10101).SerializeToString()))
r = P.HomeIndoorAreaLevelUpS2C.FromString(next(b for m, b in c.sent if m == 0x2306))
assert r.err == 1, r  # Lv.3 needs base level 5
c2 = login(940001)
info = P.HomeInfoS2C.FromString(next(b for m, b in c2.sent if m == 0x2301))
assert sorted(a.level_id for a in info.areas_in) == [1010102, 1010401], info.areas_in
print("ok")
