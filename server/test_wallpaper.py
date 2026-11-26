"""Offline test of the wallpapers (personal.py, 0x210F set / 0x2110 equip): replies echo the request as in the
Garena capture (set 1000001 type IMM -> {item_id, set_type 2}; equip -> {item_id}); the state follows the
client's own rules (ADD / DEL random list, IMM fixed + random cleared) and comes back in PersonalInfoS2C
field 15 after a new login, the rest of the message unchanged. Temporary accounts.
  python test_wallpaper.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-wallpaper-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import personal  # noqa: E402
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


def info(c):
    return P.PersonalInfoS2C.FromString(next(p for m, p in c.sent if m == 0x2101))


async def send(c, msg_id, msg):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[msg_id](c, msg.SerializeToString())
    assert [m for m, _ in c.sent] == [msg_id]
    return c.sent[0][1]


async def main():
    c = await login(9921)
    first = info(c)
    base = P.PersonalInfoS2C.FromString(c.account.b.pb("0x2101"))
    assert first.wallpaper_set == base.wallpaper_set  # untouched until the player changes it

    # the captured pair
    r = await send(c, personal.MSG_WALLPAPER_SET, P.PersonalWallpaperSetC2S(item_id=1000001, set_type=2))
    assert r == wire.build([(2, 0, 1000001), (3, 0, 2)])
    r = await send(c, personal.MSG_WALLPAPER_EQUIP, P.PersonalWallpaperEquipC2S(item_id=1000001))
    assert r == wire.build([(2, 0, 1000001)])
    w = personal.wallpaper(9921)
    assert (w.fixed, list(w.random), w.equip) == (1000001, [], 1000001)

    # random list: ADD clears the fixed one, DEL removes, IMM empties the list again
    for item in (1000002, 1000003, 1000002):
        await send(c, personal.MSG_WALLPAPER_SET, P.PersonalWallpaperSetC2S(item_id=item, set_type=0))
    w = personal.wallpaper(9921)
    assert (w.fixed, list(w.random)) == (0, [1000002, 1000003])
    await send(c, personal.MSG_WALLPAPER_SET, P.PersonalWallpaperSetC2S(item_id=1000002, set_type=1))
    assert list(personal.wallpaper(9921).random) == [1000003]

    # after a new login: the player's wallpapers in field 15, every other field as the baseline
    c2 = await login(9921)
    again = info(c2)
    assert (again.wallpaper_set.fixed, list(again.wallpaper_set.random), again.wallpaper_set.equip) == \
        (0, [1000003], 1000001)
    again.ClearField("wallpaper_set")
    base.ClearField("wallpaper_set")
    assert again == base

    await send(c2, personal.MSG_WALLPAPER_SET, P.PersonalWallpaperSetC2S(item_id=1000004, set_type=2))
    w = personal.wallpaper(9921)
    assert (w.fixed, list(w.random)) == (1000004, [])
    print("OK", TMP)


asyncio.run(main())
