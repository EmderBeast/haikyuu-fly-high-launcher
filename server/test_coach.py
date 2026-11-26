"""Offline test of the coaches (coach.py), replaying the Garena capture: 14 level-ups of coach conf 18 with the
captured consume counts and currency after each step, job buffs at 3/6/9/12/15, achievement 91202 at level 15,
then the dismantling with its refund, achievement 91204 and the captured message order. Temporary accounts.
  python test_coach.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-coach-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import coach  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
INST = 1152921504606885600
CAPTURED_AFTER = [385990, 380990, 373990, 364990, 353990, 340990, 325990, 308990, 289990, 268990, 245990, 220990,
                  193990, 164990]  # currency 336 after each level-up (0x0405 in decoded_tshark.json)


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def progress(acc, tid):
    return wire.get(wire.parse(acc.task(tid)), 3, 0)


async def main():
    c = await login(9881)
    acc = c.account
    assert not P.CoachInfoS2C.FromString(c.last(coach.MSG_INFO)).coach_cards  # a new player has no coaches
    coach._save(9881, P.CoachCard(inst_id=INST, conf_id=18, level=1))
    acc.set_currency(336, 388990)
    acc.tasks[8401507] = wire.build([(1, 0, 8401507), (2, 0, 0), (3, 0, 204)])  # 91202 [15], captured 205 after
    acc.tasks[8401512] = wire.build([(1, 0, 8401512), (2, 0, 0), (3, 0, 1691)])  # 91204, captured 1692 after

    for step, consume in enumerate(range(3000, 29001, 2000)):
        c.sent.clear()
        await server.MESSAGE_HANDLERS[coach.MSG_UP](c, P.CoachUpLevelC2S(inst_id=INST, consume_count=consume)
                                                    .SerializeToString())
        ids = [m for m, _ in c.sent]
        assert P.CoachUpLevelS2C.FromString(c.last(coach.MSG_UP)).err == 0
        card = P.CoachUpdateS2C.FromString(c.last(coach.MSG_UPDATE)).coach_cards[0]
        assert acc.get_currency(336) == CAPTURED_AFTER[step], (step, acc.get_currency(336))
        assert card.level == step + 2 and card.exp == 0
        assert len(card.job_buffs) == sum(1 for lv in (3, 6, 9, 12, 15) if lv <= card.level)
        want = [0x0405, coach.MSG_UPDATE] + ([0x0E02] if card.level == 15 else []) + [coach.MSG_UP]
        assert ids == want, (card.level, [hex(i) for i in ids])
    print("level 15 reached, buffs", [(b.job, b.buff_id) for b in card.job_buffs], "336 left", acc.get_currency(336))
    assert progress(acc, 8401507) == 205

    # at the maximum: no further level-up; too little currency for the next level: error
    await server.MESSAGE_HANDLERS[coach.MSG_UP](c, P.CoachUpLevelC2S(inst_id=INST, consume_count=31000).SerializeToString())
    assert P.CoachUpLevelS2C.FromString(c.last(coach.MSG_UP)).err != 0

    # dismantling: refund Resolve of level 15 (180800), the captured order and achievement 91204
    c.sent.clear()
    await server.MESSAGE_HANDLERS[coach.MSG_DECOMP](c, P.CoachDecompC2S(inst_ids=[INST]).SerializeToString())
    ids = [m for m, _ in c.sent]
    print("dismantle:", [hex(i) for i in ids], "336 now", acc.get_currency(336))
    assert acc.get_currency(336) == 345790 and progress(acc, 8401512) == 1692
    assert ids == [coach.MSG_UPDATE, 0x0405, server.MSG_NOTIFY_REWARDS, 0x0E02, coach.MSG_DECOMP]
    assert list(P.CoachUpdateS2C.FromString(c.sent[0][1]).del_coach_card_inst_ids) == [INST]
    assert INST not in coach.cards(9881, acc)

    # after a new login the coach list is this player's own
    c2 = await login(9881)
    assert not P.CoachInfoS2C.FromString(c2.last(coach.MSG_INFO)).coach_cards

    # a locked coach cannot be dismantled
    coach._save(9881, P.CoachCard(inst_id=INST + 1, conf_id=18, level=3, locked=True))
    await server.MESSAGE_HANDLERS[coach.MSG_DECOMP](c2, P.CoachDecompC2S(inst_ids=[INST + 1]).SerializeToString())
    assert P.CoachDecompS2C.FromString(c2.last(coach.MSG_DECOMP)).err != 0
    print("OK", TMP)


asyncio.run(main())
