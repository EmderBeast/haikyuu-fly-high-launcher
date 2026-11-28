"""Auto play / auto serve switches (0x0203 MainFlagSet): the client keeps them in infoData.flags, which only
NotifyFlagsS2C (0x0403) sets, so the server pushes the new mask before the reply; switching on and off again
works, and the mask is sent again at the next login."""
import asyncio, os, tempfile
from pathlib import Path
import accounts
TMP = Path(tempfile.mkdtemp()); accounts.ACCOUNTS = TMP
import pvp; pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
os.environ["HFH_MULTIPLAYER"] = "1"
import server, wire
P = server.matches.P


class C:
    def __init__(s, gid): s.gid, s.sent, s.writer = gid, [], None
    async def send_packet(s, mid, body=b"", **k): s.sent.append((mid, body))
    async def send_raw(s, mid, fl, body): s.sent.append((mid, body))


def login(gid):
    c = C(gid)
    asyncio.run(server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"t")])))
    return c


def flag_set(c, units):
    c.sent.clear()
    req = P.MainFlagSetC2S()
    for f, o in units:
        req.units.add(flag=f, open=o)
    asyncio.run(server.MESSAGE_HANDLERS[0x0203](c, req.SerializeToString()))
    ids = [m for m, _ in c.sent]
    push = [wire.get(wire.parse(b), 1, 0) for m, b in c.sent if m == accounts.MSG_FLAGS]
    return ids, push


ok = []
c = login(950101)
ids, push = flag_set(c, [(P.FLAG_FIGHT_AUTO_SKILL, True), (P.FLAG_FIGHT_AUTO_SERVE, True)])
ok.append(ids == [accounts.MSG_FLAGS, 0x0203] and push == [3])
print("on: push 0x0403 flags 3 before the reply", ids, push)
ids, push = flag_set(c, [(P.FLAG_FIGHT_AUTO_SKILL, False), (P.FLAG_FIGHT_AUTO_SERVE, False)])
ok.append(push == [0])
print("off: flags 0", push)
flag_set(c, [(P.FLAG_FIGHT_SPEED_ON, True)])
c2 = login(950101)
again = [wire.get(wire.parse(b), 1, 0) for m, b in c2.sent if m == accounts.MSG_FLAGS]
ok.append(again[-1:] == [4])
print("login sends the mask again", again)
print("ok" if all(ok) else "FAIL")
if not all(ok):
    raise SystemExit(1)
