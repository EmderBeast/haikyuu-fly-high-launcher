"""Offline test: a new player's start and tutorial steps as on Garena (capture neu2811 of a new guest account,
28.11.2026, data/fresh_sync.json): Garena's start state (500 000 gold, 180 stamina, 5x item 123, the Yukigaoka
six with Hinata 3101 lacking her training skill), the guided recruit (1140 locked, its profile items, one lucky
coin 250), main task 1002000 done by stage 1-3 and claimable, and the beginner 10-pull as a preview with one
re-roll and a confirm. Temporary accounts.
  python test_new_account.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

import accounts

os.environ["HFH_MULTIPLAYER"] = "1"
TMP = Path(tempfile.mkdtemp(prefix="hfh-new-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import missions  # noqa: E402
import wire  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, wire.body_to_pb(body, flags)))


def got(c, mid, T):
    return [T.FromString(p) for m, p in c.sent if m == mid]


async def main():
    c = FakeClient(920001)
    await server.handle_login(c, wire.build([(1, 0, 920001), (7, 2, b"token")]))
    cur = {x.conf_id: x.number for x in got(c, 0x0407, P.NotifyCurrencyInfoS2C)[0].currency}
    assert cur == {100: 500000, 227: 200, 105: 180}, cur
    assert [(i.conf_id, i.number) for i in got(c, 0x0801, P.PackItemInfoS2C)[0].items] == [(123, 5)]
    cards = {p.info.conf_id: list(p.info.skills) for p in got(c, 0x0901, P.PlayerCardPackS2C)[0].players}
    assert sorted(cards) == [3101, 3102, 3103, 3104, 3105, 3106], cards
    assert cards[3101] == [3101101, 3101201, 3101401] and len(cards[3102]) == 4, cards
    club = got(c, 0x2E01, P.AssociationInfoS2C)[0]
    assert club.self_number.id == 920001, club  # Garena's id replaced
    acc = c.account

    # guided recruit: lottery 1, ticket 111 from stage 1-2
    acc.set_item(111, 1)
    c.sent.clear()
    await server.MESSAGE_HANDLERS[0x0F02](c, P.LotteryDoC2S(id=1, count=1).SerializeToString())
    card = got(c, 0x0902, P.PlayerCardChangeS2C)[0].players[0].info
    assert card.conf_id == 1140 and card.locked and list(card.skills) == [1140101, 1140201, 1140401], card
    assert list(got(c, 0x2102, P.PersonalUpdateS2C)[0].item_ids) == [8114001, 8114002, 8114003, 8114004]
    reply = got(c, 0x0F02, P.LotteryDoS2C)[-1]
    assert [(i.conf_id, i.number) for i in reply.rewards.items] == [(250, 1)], reply
    assert acc.get_currency(250) == 1

    # main task "clear 1-3"
    await server.mission_event(c, missions.FINISH_MATCH, 1, (P.FIGHT_TOKYO, 10103))
    assert acc.task_state(1002000) == accounts.TASK_FINISH
    c.sent.clear()
    await server.MESSAGE_HANDLERS[0x0E03](c, P.TaskReceiveRewardsC2S(task_id=1002000).SerializeToString())
    assert not got(c, 0x0E03, P.TaskReceiveRewardsS2C)[-1].error and acc.item_count(111) == 1

    # beginner 10-pull (lottery 4, 10x item 133): preview, one free re-roll, confirm
    acc.set_item(133, 10)
    n = len(acc.owned_conf_ids())
    c.sent.clear()
    await server.MESSAGE_HANDLERS[0x0F02](c, P.LotteryDoC2S(id=4, count=10).SerializeToString())
    pre = got(c, 0x0F02, P.LotteryDoS2C)[-1]
    assert pre.is_pre and len(pre.card_inst_ids) == 10 and acc.item_count(133) == 0, pre
    assert not got(c, 0x0902, P.PlayerCardChangeS2C)  # nothing created yet
    await server.MESSAGE_HANDLERS[0x0F02](c, P.LotteryDoC2S(id=4, count=10, is_again=True).SerializeToString())
    again = got(c, 0x0F02, P.LotteryDoS2C)[-1]
    assert again.is_pre and len(again.card_inst_ids) == 10
    await server.MESSAGE_HANDLERS[0x0F02](c, P.LotteryDoC2S(id=4, count=10, is_again=True).SerializeToString())
    assert got(c, 0x0F02, P.LotteryDoS2C)[-1].err  # only one re-roll
    c.sent.clear()
    await server.MESSAGE_HANDLERS[0x0F06](c, b"")
    made = [p.info.conf_id for p in got(c, 0x0902, P.PlayerCardChangeS2C)[0].players]
    assert made == list(again.card_inst_ids), (made, again)
    assert got(c, 0x0F06, P.LotteryPreConfirmS2C)[0].id == 4
    unit = got(c, 0x0F05, P.LotteryUpdateS2C)[0].lotterys[0]
    assert unit.all_count == 10 and unit.ten_num == 1 and unit.reset_num == 1, unit
    assert len(acc.owned_conf_ids()) >= n
    print("test_new_account: OK")


asyncio.run(main())
