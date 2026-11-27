"""Offline test of the currency exchange (exchange.py, 0x1201): stamina for diamonds with the exchange.json
prices (2 free, then 50 / 75 / 75 / 100 / 100 / 150, at most 8 a day), the 0x0405 messages as in the captures
(count {type 2, num n}, then the new stamina), the day's counts in the login's 0x0407, a new day, paid diamonds
(type 3), gold (type 1) refused, and the assistant's function 5 (two free exchanges, unit 5 = (105, 60) x2 as
in wcy2711). Temporary accounts.
  python test_exchange.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-exchange-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import exchange as E  # noqa: E402
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


async def buy(c, kind=2, num=1):
    c.sent = []
    await server.MESSAGE_HANDLERS[0x1201](c, P.CurrencyExchangeC2S(exchange_type=kind, num=num).SerializeToString())
    return c.sent, P.CurrencyExchangeS2C.FromString(c.sent[-1][1])


def counts(payload):
    return [(wire.get(wire.parse(v), 1, 0), wire.get(wire.parse(v), 2, 0)) for n, _, v in wire.parse(payload) if n == 2]


async def main():
    c = await login(991201)
    sync = next(pb for mid, pb in c.sent if mid == 0x0407)
    check("login 0x0407: no exchanges today (not the baseline's {2, 2})", counts(sync) == [], str(counts(sync)))
    acc = c.account
    acc.set_currency(108, 1000)
    st = acc.get_currency(105)

    sent, rep = await buy(c)
    ids = [m for m, _ in sent]
    check("first exchange: 0x0405 count, 0x0405 stamina, reply", ids == [0x0405, 0x0405, 0x1201] and rep.err == 0
          and rep.exchange_type == 2, str([hex(i) for i in ids]))
    check("count {type 2, num 1} as captured", counts(sent[0][1]) == [(2, 1)])
    check("+60 stamina, free (diamonds unchanged)", acc.get_currency(105) == st + 60 and acc.get_currency(108) == 1000)
    sent, rep = await buy(c, num=2)  # 2nd free, 3rd 50
    check("2nd free, 3rd costs 50", rep.err == 0 and acc.get_currency(108) == 950 and acc.get_currency(105) == st + 180)
    for _ in range(5):
        await buy(c)
    check("exchanges 4-8 cost 75+75+100+100+150", acc.get_currency(108) == 950 - 500, str(acc.get_currency(108)))
    sent, rep = await buy(c)
    check("9th: over the daily limit, refused", rep.err != 0 and E.state(991201)["counts"]["2"] == 8)

    c2 = await login(991201)
    check("next login: today's count in 0x0407", counts(next(pb for mid, pb in c2.sent if mid == 0x0407)) == [(2, 8)])
    E.state(991201)["day"] -= 86400
    check("new day: counts start again", E.state(991201)["counts"] == {})

    acc.set_currency(109, 5)
    d108 = acc.get_currency(108)
    _, rep = await buy(c, kind=3, num=3)
    check("type 3: 109 x3 -> 108 x3", rep.err == 0 and acc.get_currency(109) == 2 and acc.get_currency(108) == d108 + 3)
    _, rep = await buy(c, kind=1)
    check("type 1 (gold, not in exchange.json): refused", rep.err != 0)
    acc.set_currency(108, 10)
    E.state(991201)["counts"]["2"] = 2
    _, rep = await buy(c)
    check("3rd stamina exchange without 50 diamonds: refused", rep.err != 0 and acc.get_currency(108) == 10)

    # assistant function 5 (arg 2), a fresh player as in wcy2711
    c3 = await login(991202)
    c3.sent = []
    shown = await E.assistant(c3, 2)
    check("assistant: two free exchanges, unit (105, 60) x2", shown == [(105, 60), (105, 60)], str(shown))
    shown = await E.assistant(c3, 2)
    check("assistant again: nothing more to do", shown == [])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
