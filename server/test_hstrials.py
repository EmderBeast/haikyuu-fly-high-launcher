"""Offline test of buying the High School Trials daily reward (hstrials.py, 0x5603) against the Garena capture,
on the captured account: 100 diamonds (10045 -> 9945), 0x5601 with buy_num 1, daily row 17 with its coach
boxes opened - the 3 + 3 lower coaches dismantled (336: 49000 + 3840 = 52840 as captured), the 2 orange
coaches kept as cards - 100 x245000, achievement 8401504 964 -> 966, the captured message order; the price
list's second price and the daily limit. Temporary accounts.
  python test_hstrials.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-hstrials-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import coach  # noqa: E402
import hstrials  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402
from missions import Missions  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid, acc):
        self.gid, self.sent, self.writer, self.account = gid, [], None, acc
        self.missions = Missions(acc)

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


def task_progress(acc, tid):
    return wire.get(wire.parse(acc.task(tid)), 3, 0)


async def main():
    acc = accounts.Account(9961, accounts.CAPTURED)
    c = FakeClient(9961, acc)
    acc.set_currency(108, 10045)
    before_336, before_100 = acc.get_currency(336), acc.get_currency(100)
    coaches_before = len(coach.cards(9961, acc))
    assert task_progress(acc, 8401504) == 964

    await server.MESSAGE_HANDLERS[hstrials.MSG_BUY](c, P.HighSchoolTrialsDailyRewardsBuyC2S(num=1).SerializeToString())
    ids = [m for m, _ in c.sent]
    print([hex(i) for i in ids])
    assert ids == [0x0405, 0x0E02, 0x5601, 0x0802, 0x0405, 0x5502, 0x5502, 0x0404, 0x0E02, 0x5603], ids
    assert acc.get_currency(108) == 9945
    assert P.HighSchoolTrialsInfoS2C.FromString(c.sent[2][1]).base.daily_reward_buy_num == 1
    assert acc.get_currency(336) - before_336 == 52840 and acc.get_currency(100) - before_100 == 245000
    note = P.NotifyRewardsS2C.FromString(c.sent[7][1])
    assert note.items[0].conf_id == 336 and note.items[0].number == 52840
    kept = [i.conf_id for i in note.items if 110100000 < i.conf_id < 110200000]
    assert len(kept) == 2 and all(i.conf_id // 100 in (1101001, 1101002) for i in note.items_decomp)
    assert len(note.items_decomp) == 6
    assert len(coach.cards(9961, acc)) == coaches_before + 2
    assert task_progress(acc, 8401504) == 966
    assert c.sent[-1][1] == b""

    # second buy: 150 diamonds (the list's second price)
    c.sent.clear()
    await server.MESSAGE_HANDLERS[hstrials.MSG_BUY](c, P.HighSchoolTrialsDailyRewardsBuyC2S(num=1).SerializeToString())
    assert acc.get_currency(108) == 9795 and c.sent[-1] == (0x5603, b"")

    # daily limit (constant 2: 10) and too little to pay
    pvp.state()["hstrials"]["9961"]["buys"] = hstrials.MAX_BUYS
    c.sent.clear()
    await server.MESSAGE_HANDLERS[hstrials.MSG_BUY](c, P.HighSchoolTrialsDailyRewardsBuyC2S(num=1).SerializeToString())
    assert [m for m, _ in c.sent] == [0x5603] and P.HighSchoolTrialsDailyRewardsBuyS2C.FromString(c.sent[0][1]).err == 1
    pvp.state()["hstrials"]["9961"]["buys"] = 2
    acc.set_currency(108, 10)
    c.sent.clear()
    await server.MESSAGE_HANDLERS[hstrials.MSG_BUY](c, P.HighSchoolTrialsDailyRewardsBuyC2S(num=1).SerializeToString())
    assert [m for m, _ in c.sent] == [0x5603]

    # the counter is in the login message, and resets after the day's 06:00
    info = P.HighSchoolTrialsInfoS2C.FromString(hstrials.info(9961, acc))
    assert info.base.daily_reward_buy_num == 2 and len(info.proresses) > 0
    st = pvp.state()["hstrials"]["9961"]
    assert hstrials.base(9961, acc, now=st["reset"] + 1).daily_reward_buy_num == 0
    print("OK", TMP)


asyncio.run(main())
