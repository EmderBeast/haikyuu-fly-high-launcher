"""
Memory gallery: 0x3C09 RecallingIllustratedC2S -> RecallingIllustratedS2C{data = [{id, timestamp}]}.

Shown by the Garena captures (Haikyu-Fly-High-Analyse/capture, 4 replies of 2 accounts, each compared
with the RecallingInfoS2C of the same login):
  - one entry per kind of memory (PlayerRecalling.conf_id) the account ever had: every kind it holds is
    listed, and kinds it no longer holds stay listed (1 and 8 such entries)
  - timestamp = when the first memory of that kind was obtained: equal to the create_time of the oldest
    copy held in 29 / 44 kinds, earlier in the rest (the first copy was used up), never later

Before this the server sent every player the captured gallery of the owner account.

Assumption (no capture of a gallery right after a new memory): a kind is entered when the server next
looks at the account's memories (login, opening the gallery), with the create_time of its oldest copy.
A memory obtained and used up between two such looks is therefore missed.
"""
import logging

import matches
import pvp
import wire
from accounts import CAPTURED

P = matches.P
log = logging.getLogger("hfh")

MSG_ILLUSTRATED = 0x3C09


def _captured() -> dict[int, int]:
    """The owner account's gallery as Garena sent it."""
    reply = pvp.HOOKS.get("replies", {}).get("0x3C09")
    if not reply:
        return {}
    msg = P.RecallingIllustratedS2C()
    try:
        msg.ParseFromString(wire.body_to_pb(bytes.fromhex(reply["body_hex"]), reply["flags"]))
    except Exception:
        return {}
    return {d.id: d.timestamp for d in msg.data}


def book(client) -> dict[str, int]:
    """kind -> time first obtained, brought up to date with the memories the account holds now."""
    books = pvp.state().setdefault("memorybook", {})
    d = books.get(str(client.gid))
    acc = client.account
    if d is None:  # the captured account starts with Garena's gallery, everyone else empty
        d = books[str(client.gid)] = {str(k): v for k, v in (_captured() if acc.b is CAPTURED else {}).items()}
    changed = False
    for raw in acc.all_recallings().values():
        r = P.PlayerRecalling.FromString(raw)
        key = str(r.conf_id)
        if r.conf_id and (key not in d or (r.create_time and r.create_time < d[key])):
            d[key] = r.create_time
            changed = True
    if changed:
        pvp.save()
    return d


async def handle_illustrated(client, pb: bytes):
    out = P.RecallingIllustratedS2C()
    for kind, ts in book(client).items():
        out.data.add(id=int(kind), timestamp=ts)
    await client.send_packet(MSG_ILLUSTRATED, out.SerializeToString())
    log.info(f"  MEMORY GALLERY: {len(out.data)} kinds")


def on_login(client) -> list:
    book(client)
    return []


HANDLERS = {MSG_ILLUSTRATED: handle_illustrated}
