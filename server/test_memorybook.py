"""Offline test of the memory gallery (0x3C09 RecallingIllustratedC2S), against what the Garena captures
show (see memorybook.py). Temporary accounts, no port.
  python test_memorybook.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-membook-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import memorybook  # noqa: E402
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


async def gallery(c):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[memorybook.MSG_ILLUSTRATED](c, b"")
    msg = P.RecallingIllustratedS2C.FromString(c.sent[-1][1])
    return {d.id: d.timestamp for d in msg.data}


def memory(acc, kind, created):
    raw = acc.add_recalling(kind)
    raw = wire.build(wire.replace(wire.parse(raw), 9, created))
    acc.put_recalling(raw)
    return wire.get(wire.parse(raw), 1)


async def main():
    # a new player: not the owner's captured gallery
    c = FakeClient(990601)
    await server.handle_login(c, wire.build([(1, 0, 990601), (7, 2, b"token")]))
    acc = c.account
    held = {P.PlayerRecalling.FromString(r).conf_id for r in acc.all_recallings().values()}
    g = await gallery(c)
    check("new player: only the kinds he holds", set(g) == held, f"{len(g)} kinds, holds {len(held)}")

    first = memory(acc, 7, 1000)
    memory(acc, 7, 500)
    memory(acc, 9, 2000)
    g = await gallery(c)
    check("a kind is listed once, with the time of its oldest copy", g.get(7) == 500 and g.get(9) == 2000, str(g))

    # the oldest copy is used up: the entry and its time stay
    for inst in [i for i, r in acc.recallings.items() if P.PlayerRecalling.FromString(r).conf_id == 7]:
        del acc.recallings[inst]
    g = await gallery(c)
    check("a kind no longer held stays listed", g.get(7) == 500, str(g))
    memory(acc, 7, 9000)
    g = await gallery(c)
    check("a later copy does not move the time", g.get(7) == 500)
    check("no error in the reply", P.RecallingIllustratedS2C.FromString(c.sent[-1][1]).err == 0 and first)

    # the captured account: Garena's gallery, plus what it gets here
    owner = FakeClient(990602)
    owner.account = accounts.Account(990602, accounts.CAPTURED)
    captured = memorybook._captured()
    g = await gallery(owner)
    owned = {}
    for r in owner.account.all_recallings().values():
        m = P.PlayerRecalling.FromString(r)
        owned[m.conf_id] = min(owned.get(m.conf_id, 1 << 62), m.create_time)
    check("captured account: every captured entry is there", bool(captured) and all(k in g for k in captured),
          f"{len(captured)} captured, {len(g)} sent")
    check("captured times are kept unless an older copy is held",
          all(g[k] == min(v, owned.get(k, v)) for k, v in captured.items()))
    new_kind = max(g) + 1
    memory(owner.account, new_kind, 4242)
    g2 = await gallery(owner)
    check("a kind obtained here is added", g2.get(new_kind) == 4242 and len(g2) == len(g) + 1)

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    raise SystemExit(0 if all(checks) else 1)


asyncio.run(main())
