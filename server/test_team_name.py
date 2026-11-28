"""Offline test of renaming a line-up (0x0B05 TeamNameC2S, server.handle_team_name). Temporary accounts, no port.
  python test_team_name.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-teamname-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def main():
    c = FakeClient(991101)
    await server.handle_login(c, wire.build([(1, 0, 991101), (7, 2, b"token")]))
    c.sent.clear()
    req = P.TeamNameC2S(formation_name=P.FormationName(lineup=2, name="Power"))
    await server.MESSAGE_HANDLERS[0x0B05](c, req.SerializeToString())
    r = P.TeamNameS2C.FromString(c.sent[-1][1])
    check("reply: no error, the name back", r.error == 0 and r.formation_name.lineup == 2 and r.formation_name.name == "Power")
    names = {f.lineup: f.name for f in P.TeamInfoS2C.FromString(c.account.team_info()).formation_name_sli}
    check("kept in TeamInfoS2C for the next login", names.get(2) == "Power", names)
    await server.MESSAGE_HANDLERS[0x0B05](c, P.TeamNameC2S(formation_name=P.FormationName(lineup=2, name="Receive")).SerializeToString())
    names = [f for f in P.TeamInfoS2C.FromString(c.account.team_info()).formation_name_sli if f.lineup == 2]
    check("renamed again: one entry, the new name", len(names) == 1 and names[0].name == "Receive")
    formations = P.TeamInfoS2C.FromString(c.account.team_info()).formations
    check("line-ups untouched", len(formations) > 0)
    await server.MESSAGE_HANDLERS[0x0B05](c, P.TeamNameC2S(formation_name=P.FormationName(lineup=1, name=" ")).SerializeToString())
    check("empty name: refused", P.TeamNameS2C.FromString(c.sent[-1][1]).error != 0)
    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
