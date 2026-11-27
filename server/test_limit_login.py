"""Offline test of the limited-time login stamina (limitlogin.py, local activity type 1): own state at login,
claiming the evening stamina as in the Garena capture (decoded_tshark.json 2811-2814: update {noon/night},
stamina 105 +60, popup 105 x60, reply {type 1}), claimed twice refused, reissue with its cost 108 x20,
a new day at 06:00 UTC opens both again. Temporary accounts.
  python test_limit_login.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-limitlogin-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import limitlogin as L  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


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


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def type1(payload, cls=P.LocalActivityInfoS2C):
    for a in cls.FromString(payload).activity_list:
        if a.type == 1:
            return P.ActivityLimitLoginData.FromString(a.data)
    return None


async def claim(c, slot, back=False):
    c.sent = []
    data = P.ActivityLimitLoginReward(reward_type=slot, is_reward_back=back).SerializeToString()
    await server.MESSAGE_HANDLERS[0x2803](c, P.LocalActivityRewardC2S(type=1, data=data).SerializeToString())
    return c.sent, P.LocalActivityRewardS2C.FromString(c.sent[-1][1])


def popup(sent):
    for mid, pb in sent:
        if mid == 0x0404:
            return [(wire.get(wire.parse(v), 2, 0), wire.get(wire.parse(v), 3, 0)) for n, _, v in wire.parse(pb) if n == 1]
    return None


async def main():
    c = await login(990901)
    d0 = type1(next(pb for mid, pb in c.sent if mid == 0x2801))
    check("login: today's noon and evening stamina open (not the capture's GOT)",
          d0 is not None and d0.noon_time_state == 0 and d0.night_time_state == 0, str(d0).replace("\n", " "))

    acc = c.account
    st0 = acc.get_currency(105)
    sent, rep = await claim(c, 1)  # the capture's request {1=1}
    ids = [m for m, _ in sent]
    check("order as captured: 0x2802, 0x0405, 0x0404, 0x2803", ids == [0x2802, 0x0405, 0x0404, 0x2803], str([hex(i) for i in ids]))
    upd = type1(sent[0][1], P.LocalActivityUpdateS2C)
    check("evening GOT, noon still open", upd.night_time_state == 2 and upd.noon_time_state == 0)
    check("reply {type 1} without error", rep.type == 1 and rep.err == 0)
    check("popup and stamina: 105 x60", popup(sent) == [(105, 60)] and acc.get_currency(105) - st0 == 60,
          f"{popup(sent)}, {acc.get_currency(105) - st0}")
    _, rep = await claim(c, 1)
    check("evening twice: refused", rep.err != 0)

    acc.set_currency(108, 100)
    sent, rep = await claim(c, 0, back=True)  # noon reissued
    check("reissue: ok, costs 108 x20", rep.err == 0 and acc.get_currency(108) == 80, str(acc.get_currency(108)))
    check("both GOT", type1(sent[-4][1] if sent[0][0] != 0x2802 else sent[0][1], P.LocalActivityUpdateS2C) is not None
          and L.state(990901)["noon"] == 2 and L.state(990901)["night"] == 2)

    acc.set_currency(108, 5)
    L.state(990901)["noon"] = 0
    _, rep = await claim(c, 0, back=True)
    check("reissue without enough 108: refused, nothing taken", rep.err != 0 and acc.get_currency(108) == 5)

    c = await login(990901)
    d1 = type1(next(pb for mid, pb in c.sent if mid == 0x2801))
    check("same day: the login shows the claimed evening", d1.night_time_state == 2)
    L.state(990901)["day"] -= 86400
    d2 = L.state(990901)
    check("new day (06:00 UTC): both open again", d2["noon"] == 0 and d2["night"] == 0)
    _, rep = await claim(c, 2)
    check("unknown slot: refused", rep.err != 0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
