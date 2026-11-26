"""Offline test of the Extreme Training idle reward (hangup.py) against the Garena capture: 11 periods since the
captured hang_up_start give 8811 x item 102 and 2332 x item 127 (floor 655, Award 801 / 212 per 300 s), pushes
carry only the changed entry (index 1 first), a claim sends 0x1702 (new start, entries at 0), 0x0405, pop-up and
the empty 0x1704; a period after a floor change still credits the old floor, the next one the new floor; at most
72 periods (21600 s) accrue. Temporary accounts.
  python test_hangup.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-hangup-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import hangup  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
START = 1795383267  # captured hang_up_start_time


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


def rewards(body, cls=P.FightConTestInfoS2C, field="hang_up_rewards"):
    return {(r.contest_id, r.index): r.number for r in getattr(cls.FromString(body), field)}


async def claim(c, now):
    hangup.time.time = lambda: now
    c.sent.clear()
    await server.MESSAGE_HANDLERS[hangup.MSG_CLAIM](c, b"")
    return c.sent


async def main():
    c = await login(9901)
    acc = c.account
    info = P.FightConTestInfoS2C.FromString(next(p for m, p in c.sent if m == hangup.MSG_INFO))
    assert not info.hang_up_rewards and info.contest.hang_up_start_time != START  # own state, not the capture
    assert acc.contest_floor() == 655

    acc.hangup = {"start": START, "last": START, "floor": 655, "rewards": {}}
    assert rewards(hangup.info(acc, 1795386558)) == {(655, 0): 8811, (655, 1): 2332}  # captured login
    pushes = hangup.push_messages(acc, 1795386650)
    got = [rewards(b, P.FightConTestUpdateS2C, "change_hang_up_rewards") for b in pushes]
    assert got == [{(655, 1): 2544}, {(655, 0): 9612}], got  # captured 0x1702 pushes, index 1 first

    sent = await claim(c, 1795387043)
    ids = [m for m, _ in sent]
    assert ids == [hangup.MSG_UPDATE, hangup.MSG_UPDATE, hangup.MSG_UPDATE, 0x0405, server.MSG_NOTIFY_REWARDS,
                   hangup.MSG_CLAIM], [hex(i) for i in ids]
    upd = P.FightConTestUpdateS2C.FromString(sent[2][1])
    assert upd.contest.hang_up_start_time == 1795387043 and upd.contest.id == 655
    assert rewards(sent[2][1], P.FightConTestUpdateS2C, "change_hang_up_rewards") == {(655, 0): 0, (655, 1): 0}
    popup = [(wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0)) for _, _, v in wire.parse(sent[4][1])]
    assert popup == [(102, 13 * 801), (127, 13 * 212)], popup  # 2756 = captured coins of this claim
    assert sent[5][1] == b""

    # floor 657 cleared at 1795387261: the period ending at ...7200 (processed later) still credits 655
    acc.clear_contest(657)
    sent = await claim(c, 1795387287)
    popup = [(wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0)) for _, _, v in wire.parse(sent[-2][1])]
    assert popup == [(102, 801), (127, 212)], popup
    assert rewards(hangup.info(acc, 1795387650)) == {(657, 0): 801, (657, 1): 212}  # next period: floor 657

    # cap: never more than 72 periods since the start
    assert rewards(hangup.info(acc, 1795387287 + 10 * 86400)) == {(657, 0): 72 * 801, (657, 1): 72 * 212}

    # after a new login the stored rewards and start come back
    c2 = await login(9901)
    info = P.FightConTestInfoS2C.FromString(next(p for m, p in c2.sent if m == hangup.MSG_INFO))
    assert info.contest.hang_up_start_time == 1795387287
    assert {(r.contest_id, r.index): r.number for r in info.hang_up_rewards}[(657, 1)] >= 72 * 212
    print("OK", TMP)


asyncio.run(main())
