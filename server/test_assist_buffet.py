"""Assistant function 14 (club buffet donations, clubs.assistant_buffet) against the Garena capture wcy2711:
foods 205000001 x1, 205000002 x7, 205000003 x7, 205000004 x12 -> donated 1 / 7 / 2 / 0 (ascending food id,
10 a day), unit 14 = DONOR_REWARD per food times its donations, 0x2E1D before the item changes; a second run
does nothing. Temporary accounts.
  python test_assist_buffet.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-assistbuffet-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import clubs as C  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []
GARENA_14 = [(128, 10), (129, 30), (130, 15), (128, 70), (129, 210), (130, 105), (128, 20), (129, 60), (130, 30)]


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


def units(payload):
    out = {}
    for n, _, v in wire.parse(payload):
        if n == 2:
            u = wire.parse(v)
            out[wire.get(u, 1)] = [(wire.get(wire.parse(i), 2), wire.get(wire.parse(i), 3)) for k, _, i in u if k == 3]
    return out


async def main():
    c = FakeClient(991301)
    await server.handle_login(c, wire.build([(1, 0, 991301), (7, 2, b"token")]))
    acc = c.account
    acc.set_currency(108, 1000)
    await server.MESSAGE_HANDLERS[C.MSG_CREATE](c, P.AssociationCreateC2S(name="Inarizaki", icon_id=1).SerializeToString())
    await asyncio.sleep(0.05)
    for food, n in ((205000001, 1), (205000002, 7), (205000003, 7), (205000004, 12)):
        acc.set_item(food, n)
    c.sent = []
    shown = await C.assistant_buffet(c)
    check("unit 14 = Garena's (per food, not summed)", shown == GARENA_14, str(shown))
    check("foods left as in the capture: 0 / 0 / 5 / 12",
          [acc.item_count(f) for f in (205000001, 205000002, 205000003, 205000004)] == [0, 0, 5, 12])
    ids = [m for m, _ in c.sent]
    check("0x2E1D first, then one 0x0802 per food", ids[:4] == [C.MSG_BUFFET_INFO, 0x0802, 0x0802, 0x0802], str([hex(i) for i in ids[:5]]))
    check("10 donations booked", C._buffet_me(C.club_of(991301), 991301)["donated"] == 10)
    check("second run: nothing to donate", await C.assistant_buffet(c) == [])

    # through the assistant (function 14 switched on)
    c2 = FakeClient(991302)
    await server.handle_login(c2, wire.build([(1, 0, 991302), (7, 2, b"token")]))
    c2.account.set_currency(108, 1000)
    await server.MESSAGE_HANDLERS[C.MSG_CREATE](c2, P.AssociationCreateC2S(name="Shiratorizawa", icon_id=1).SerializeToString())
    await asyncio.sleep(0.05)
    c2.account.set_item(205000001, 3)
    await server.handle_share_assist_set(c2, wire.build([(1, 2, wire.build([(1, 0, 14), (2, 0, 1)]))]))
    c2.sent = []
    await server.handle_share_assist_do(c2, b"")
    got = units(next(pb for mid, pb in c2.sent if mid == server.MSG_ASSIST_DO))
    check("assistant run: unit 14 for 3 donations", got.get(14) == [(128, 30), (129, 90), (130, 45)], str(got.get(14)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
