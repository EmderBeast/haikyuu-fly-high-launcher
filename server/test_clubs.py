"""Offline test of the clubs (clubs.py): create (300 diamonds), list / search, join an open club,
request + approve in a reviewed club, roles, notice, daily welfare, club chat, kick, leave, hand over,
disband. Temporary accounts.
  python test_clubs.py
"""
import asyncio
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-clubs-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import chat  # noqa: E402
import clubs as C  # noqa: E402
import wire  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def all(self, msg_id):
        return [p for m, p in self.sent if m == msg_id]

    def last(self, msg_id):
        return self.all(msg_id)[-1]

    def info(self):
        return P.AssociationInfoS2C.FromString(self.last(C.MSG_INFO))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def call(c, msg_id, msg=b""):
    await server.MESSAGE_HANDLERS[msg_id](c, msg if isinstance(msg, bytes) else msg.SerializeToString())
    await asyncio.sleep(0.05)  # pushes run as tasks


def err(c, msg_id, cls):
    return getattr(P, cls).FromString(c.last(msg_id)).err


async def main():
    a, b, c = await login(9821), await login(9822), await login(9823)
    assert not a.info().HasField("association")

    # create: costs 300 diamonds
    a.account.set_currency(C.CREATE_COST[0], 500)
    await call(a, C.MSG_CREATE, P.AssociationCreateC2S(name="Karasuno", read_str="Fly!", icon_id=2))
    assert err(a, C.MSG_CREATE, "AssociationCreateS2C") == 0
    assert a.account.get_currency(C.CREATE_COST[0]) == 200
    club = a.info().association
    print("club", club.id, club.name, club.cur_count, club.leader_name)
    assert club.name == "Karasuno" and club.cur_count == 1 and club.icon_id == 2
    await call(b, C.MSG_CREATE, P.AssociationCreateC2S(name="karasuno"))  # name taken / no diamonds
    assert err(b, C.MSG_CREATE, "AssociationCreateS2C") != 0

    await call(b, C.MSG_LIST)
    assert [o.name for o in P.AssociationListS2C.FromString(b.last(C.MSG_LIST)).association] == ["Karasuno"]
    await call(b, C.MSG_SELECT, P.AssociationSelectC2S(condtion=P.ASSOCIATION_NAME, name="kara"))
    assert P.AssociationSelectS2C.FromString(b.last(C.MSG_SELECT)).association[0].id == club.id
    await call(b, C.MSG_SELECT, P.AssociationSelectC2S(condtion=P.ASSOCIATION_ID, value=club.id))
    assert P.AssociationSelectS2C.FromString(b.last(C.MSG_SELECT)).association[0].id == club.id

    # open club: b joins at once, a sees the new member
    await call(b, C.MSG_APPLY, P.AssociationApplyC2S(association_ids=[club.id]))
    assert P.AssociationApplyS2C.FromString(b.last(C.MSG_APPLY)).is_add
    assert {n.base.id for n in a.info().numbers} == {9821, 9822} and b.info().association.id == club.id

    # reviewed club: c's request waits, a approves
    await call(a, C.MSG_REVIEW, P.AssociationReviewC2S(condition=P.ASSOCIATION_ALL_REVIEW, value=0))
    await call(c, C.MSG_APPLY, P.AssociationApplyC2S(association_ids=[club.id]))
    assert not P.AssociationApplyS2C.FromString(c.last(C.MSG_APPLY)).is_add
    assert [r.friend.gid for r in a.info().reviews] == [9823]
    assert not b.info().reviews  # normal members don't see requests
    await call(a, C.MSG_JOIN, P.AssociationJoinC2S(target_id=[9823], action=True))
    assert c.info().association.id == club.id and len(a.info().numbers) == 3

    # roles: b vice leader, b kicks... only normal members; a hands over to b
    await call(a, C.MSG_APPOINT, P.AssociationAppointC2S(appoint=P.ASSOCIATION_VICE_LEADER, terget_id=9822))
    assert b.info().self_number.position == P.ASSOCIATION_VICE_LEADER
    await call(b, C.MSG_EXIT, P.AssociationExitC2S(target_id=9821))  # vice can't kick the leader
    assert err(b, C.MSG_EXIT, "AssociationExitS2C") != 0

    # notice, welfare, club chat
    await call(a, C.MSG_NOTIFY, P.AssociationNotifyC2S(title="Training", content="Heute 20 Uhr"))
    assert c.info().association_ex.notify[0].content == "Heute 20 Uhr"
    await call(c, C.MSG_DAILY)
    assert err(c, C.MSG_DAILY, "AssociationDailyFreeS2C") == 0
    await call(c, C.MSG_DAILY)
    assert err(c, C.MSG_DAILY, "AssociationDailyFreeS2C") != 0  # once a day
    await call(c, chat.MSG_SEND, P.ChatMsgSendC2S(chan=P.ASSOCIATION_CHAN, content="hallo club"))
    for x in (a, b, c):
        upd = P.ChatUpdateS2C.FromString(x.last(chat.MSG_UPDATE))
        assert upd.association_msg_list[0].content == "hallo club"
    assert upd.new_chat_person[0].association_name == "Karasuno"

    # kick c (leader), c gets the kick message
    await call(a, C.MSG_EXIT, P.AssociationExitC2S(target_id=9823))
    assert P.AssociationExitS2C.FromString(c.last(C.MSG_EXIT)).target_id == 9823
    assert not c.info().HasField("association") and len(a.info().numbers) == 2
    await call(c, C.MSG_APPLY, P.AssociationApplyC2S(association_ids=[club.id]))  # 24 h wait
    assert err(c, C.MSG_APPLY, "AssociationApplyS2C") != 0

    # the leader leaves: b (vice) takes over; b alone leaves: club gone
    await call(a, C.MSG_EXIT, P.AssociationExitC2S(target_id=9821))
    assert P.AssociationExitS2C.FromString(a.last(C.MSG_EXIT)).is_exit
    assert b.info().self_number.position == P.ASSOCIATION_LEADER and len(b.info().numbers) == 1
    b = await login(9822)  # state after a new login
    assert b.info().association.name == "Karasuno"
    await call(b, C.MSG_DISBAND)
    assert b.all(C.MSG_DISBAND) and not b.info().HasField("association")
    assert not C._st()["clubs"]
    print("OK", TMP)


asyncio.run(main())
