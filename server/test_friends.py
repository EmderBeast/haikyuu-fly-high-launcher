"""Offline test of the friends list (friends.py): search, request, accept, friendship tokens, remove,
block (also blocks private chat), state after a new login. Temporary accounts.
  python test_friends.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-friends-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import chat  # noqa: E402
import friends as F  # noqa: E402
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


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def call(c, msg_id, msg):
    await server.MESSAGE_HANDLERS[msg_id](c, msg if isinstance(msg, bytes) else msg.SerializeToString())
    await asyncio.sleep(0.05)  # pushes run as tasks


async def main():
    a, b, c = await login(9811), await login(9812), await login(9813)
    name_b = pvp.state()["snapshots"]["9812"]["name"]

    await call(a, F.MSG_SEARCH, P.FriendSearchC2S(name=name_b[-4:]))
    hits = P.FriendSearchS2C.FromString(a.last(F.MSG_SEARCH)).data
    print("search", name_b[-4:], "->", [(h.data.gid, h.data.name, h.state) for h in hits])
    assert [h.data.gid for h in hits] == [9812] and hits[0].state == P.ONLINE
    await call(a, F.MSG_RECOMMEND, b"")
    rec = {r.gid for r in P.FriendRecommendUpdateS2C.FromString(a.last(F.MSG_RECOMMEND)).recommend_list}
    assert 9811 not in rec and rec

    # request -> b sees it at once, accepts -> both get the new friend
    await call(a, F.MSG_APPLY, P.FriendApplyC2S(apply_gid=9812))
    assert P.FriendApplyS2C.FromString(a.last(F.MSG_APPLY)).err == 0
    inv = P.FriendInviteDataUpdateS2C.FromString(b.last(F.MSG_INVITE))
    assert inv.friend.base.gid == 9811
    await call(b, F.MSG_HANDLE, P.FriendApplyHandleC2S(handle_type=P.FRIEND_HANDLE_AGREE, handle_gids=[9811]))
    assert list(P.FriendApplyHandleS2C.FromString(b.last(F.MSG_HANDLE)).handle_gid) == [9811]
    for x, other in ((a, 9812), (b, 9811)):
        upd = P.FriendDataUpdateS2C.FromString(x.last(F.MSG_UPDATE))
        assert upd.up_type == P.FRIEND_UPDATE_NEW and upd.friend_list[0].base.gid == other
    await call(a, F.MSG_APPLY, P.FriendApplyC2S(apply_gid=9812))  # already friends
    assert P.FriendApplyS2C.FromString(a.last(F.MSG_APPLY)).err != 0

    # friendship: a sends, b collects Friendship Tokens (item 115)
    await call(a, F.MSG_SEND, P.FriendshipSendC2S(send_all=True))
    assert list(P.FriendshipSendS2C.FromString(a.last(F.MSG_SEND)).friendship_gids) == [9812]
    assert P.FriendshipUpdateS2C.FromString(b.last(F.MSG_COUNT)).friendship_wait_get == 1
    tokens = b.account.item_count(F.TOKEN)
    await call(b, F.MSG_GET, b"")
    print("tokens", tokens, "->", b.account.item_count(F.TOKEN))
    assert b.account.item_count(F.TOKEN) == tokens + F.TOKENS_PER_GIFT
    cnt = P.FriendshipUpdateS2C.FromString(b.last(F.MSG_COUNT))
    assert cnt.friendship_wait_get == 0 and cnt.friendship_got == 1

    # new login: everything is there
    a = await login(9811)
    info = P.FriendInfoS2C.FromString(a.last(F.MSG_INFO))
    print("a's friends", [(f.base.gid, f.base.name, f.state) for f in info.friend_list], "sent", list(info.friendship_gids))
    assert [f.base.gid for f in info.friend_list] == [9812] and list(info.friendship_gids) == [9812]

    # ignore a request, remove a friend, block
    await call(c, F.MSG_APPLY, P.FriendApplyC2S(apply_gid=9811))
    await call(a, F.MSG_HANDLE, P.FriendApplyHandleC2S(handle_type=P.FRIEND_HANDLE_IGNORE, handle_gids=[9813]))
    assert not F.me(9811)["invites"] and "9813" not in F.me(9811)["friends"]
    await call(a, F.MSG_REMOVE, P.FriendRemoveC2S(gid=9812))
    assert P.FriendDataUpdateS2C.FromString(b.last(F.MSG_UPDATE)).up_type == P.FRIEND_UPDATE_DEL
    assert not F.me(9812)["friends"]
    await call(b, F.MSG_BLACK, P.FriendBlackHandleC2S(handle_type=P.BLACK_ADD, gid=9813))
    assert P.FriendBlackHandleS2C.FromString(b.last(F.MSG_BLACK)).friend_black.friend.base.gid == 9813
    await call(c, F.MSG_APPLY, P.FriendApplyC2S(apply_gid=9812))  # blocked: no request
    assert P.FriendApplyS2C.FromString(c.last(F.MSG_APPLY)).err != 0
    n = len(b.all(chat.MSG_UPDATE))
    await call(c, chat.MSG_SEND, P.ChatMsgSendC2S(chan=P.FRIEND_CHAN, arg=9812, content="hallo?"))
    assert len(b.all(chat.MSG_UPDATE)) == n  # blocked: the private message is not delivered
    b = await login(9812)
    assert [x.friend.base.gid for x in P.FriendInfoS2C.FromString(b.last(F.MSG_INFO)).black_list] == [9813]
    await call(b, F.MSG_BLACK, P.FriendBlackHandleC2S(handle_type=P.BLACK_DEL, gid=9813))
    assert not F.me(9812)["black"]

    # another player's profile (0x210D): b's own choices, showcase cards, unknown player -> err
    import personal
    cards = [P.PlayerCard.FromString(x) for x in personal._all_cards(b.account)][:2]
    await call(b, 0x2107, P.PersonalSentenceSetC2S(sentence="Fly high!"))
    await call(b, 0x2108, P.PersonalShowPlayerListSetC2S(inst_ids=[x.inst_id for x in cards]))
    await call(a, personal.MSG_OTHER, P.PersonalOtherC2S(gid=9812))
    prof = P.PersonalOtherS2C.FromString(a.last(personal.MSG_OTHER))
    print("profile", prof.gid, prof.name, prof.level, repr(prof.sentence), [x.info.conf_id for x in prof.show_players],
          prof.card_record_sum, prof.arena_fight_score)
    assert prof.err == 0 and prof.gid == 9812 and prof.name == name_b and prof.sentence == "Fly high!"
    assert [x.info.inst_id for x in prof.show_players] == [x.inst_id for x in cards] and prof.card_record_sum > 0
    assert prof.setting >> P.PERSONAL_INFO_SHOW & 1  # public by default (baseline bits 124)
    # settings: stored, echoed, private profile, no friend requests; sent again after login
    await call(c, 0x2402, P.SettingModifyC2S(setting_data=P.SettingData(bits_data=0)))
    assert P.SettingModifyS2C.FromString(c.last(0x2402)).setting_data.bits_data == 0
    await call(a, personal.MSG_OTHER, P.PersonalOtherC2S(gid=9813))
    assert P.PersonalOtherS2C.FromString(a.last(personal.MSG_OTHER)).setting == 0
    await call(a, F.MSG_APPLY, P.FriendApplyC2S(apply_gid=9813))
    assert P.FriendApplyS2C.FromString(a.last(F.MSG_APPLY)).err != 0  # c takes no friend requests
    c = await login(9813)
    assert P.SettingInfoS2C.FromString(c.last(0x2401)).setting_data.bits_data == 0
    del server.mail.ONLINE[9812]  # offline: loaded from disk
    await call(a, personal.MSG_OTHER, P.PersonalOtherC2S(gid=9812))
    assert P.PersonalOtherS2C.FromString(a.last(personal.MSG_OTHER)).sentence == "Fly high!"
    await call(a, personal.MSG_OTHER, P.PersonalOtherC2S(gid=4242))
    assert P.PersonalOtherS2C.FromString(a.last(personal.MSG_OTHER)).err != 0
    print("OK", TMP)


asyncio.run(main())
