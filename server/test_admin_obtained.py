"""Cards from the admin / creative mode count like drawn ones: favor list (Switch Player), the players'
achievements finished, and profile items looked at (0x210E) lose their red dot over a new login."""
import asyncio, os, tempfile
from pathlib import Path
import accounts
TMP = Path(tempfile.mkdtemp()); accounts.ACCOUNTS = TMP
import pvp; pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
os.environ["HFH_MULTIPLAYER"] = "1"
import server, wire, admin, gameconfig
P = server.matches.P


class C:
    def __init__(s, gid): s.gid, s.sent, s.writer = gid, [], None
    async def send_packet(s, mid, body=b"", **k): s.sent.append((mid, body))
    async def send_raw(s, mid, fl, body): s.sent.append((mid, body))


def login(gid):
    c = C(gid)
    asyncio.run(server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"t")])))
    return c


c = login(950001)
admin._apply(c.account, {"cards": [1101]})
admin._apply(c.account, {"challenges": True})
admin._apply(c.account, {"functions": True})
c.account.admin_flags["creative"] = 1
c.account.save()
tid = gameconfig.PLAYERS[1101]["PlayerChallengeTask"][0]
assert c.account.task_state(tid) == accounts.TASK_FINISH
asyncio.run(server.MESSAGE_HANDLERS[0x210E](c, P.PersonalTouchC2S(item_ids=[2001, 2002]).SerializeToString()))
r = P.PersonalTouchS2C.FromString(next(b for m, b in c.sent if m == 0x210E))
assert list(r.touched_item_ids) == [2001, 2002]
c2 = login(950001)
fav = P.FavorInfoS2C.FromString(next(b for m, b in c2.sent if m == 0x1C01))
assert 1101 in [b.conf_id for b in fav.base], fav
info = P.PersonalInfoS2C.FromString(next(b for m, b in c2.sent if m == 0x2101))
assert {2001, 2002} <= set(info.touched_item_id)
tasks = P.TaskChangeS2C.FromString(next(b for m, b in reversed(c2.sent) if m == 0x0E02))
assert any(t.id == tid and t.state == 1 for t in tasks.tasks), "challenge task not sent"
import guide
g = P.UserGuideInfoS2C.FromString(next(b for m, b in c2.sent if m == 0x1F01))
assert set(guide.all_groups()) <= set(g.done_group_ids), "tutorial not off"
tok = P.FightTokyoInfoS2C.FromString(next(b for m, b in reversed(c2.sent) if m == 0x1101))
assert 10203 in tok.stages_pass, tok
print("ok")
