"""Offline test of the chat between players (chat.py): world message to every other online player with
the sender's name, private message, history after a new login, read marker. Temporary accounts.
  python test_chat.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-chat-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import mail  # noqa: E402
import chat  # noqa: E402
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


async def send(c, **kw):
    await server.MESSAGE_HANDLERS[chat.MSG_SEND](c, P.ChatMsgSendC2S(**kw).SerializeToString())
    await asyncio.sleep(0.05)  # pushes run as tasks
    return P.ChatMsgSendS2C.FromString(c.last(chat.MSG_SEND))


async def main():
    a, b, c = await login(9801), await login(9802), await login(9803)
    name_a = pvp.state()["snapshots"]["9801"]["name"]

    r = await send(a, chan=P.WORLD_CHAN, content="Hallo zusammen")
    assert r.err == 0
    upd = P.ChatUpdateS2C.FromString(b.last(chat.MSG_UPDATE))
    print("world at b:", [(u.gid, u.content) for u in upd.world_msg_list], [p.name for p in upd.new_chat_person])
    assert upd.world_msg_list[0].content == "Hallo zusammen" and upd.new_chat_person[0].name == name_a
    assert c.all(chat.MSG_UPDATE) and a.all(chat.MSG_UPDATE)  # everyone, the sender too (echo)

    r = await send(a, chan=P.FRIEND_CHAN, arg=9802, content="nur für dich")
    assert r.err == 0
    upd = P.ChatUpdateS2C.FromString(b.last(chat.MSG_UPDATE))
    assert upd.chat_list[0].friend_gid == 9801 and upd.chat_list[0].chat_list[0].content == "nur für dich"
    assert len(c.all(chat.MSG_UPDATE)) == 1  # the private message did not reach c
    echo = P.ChatUpdateS2C.FromString(a.last(chat.MSG_UPDATE))  # the sender gets it back, filed under b
    assert echo.chat_list[0].friend_gid == 9802 and echo.chat_list[0].chat_list[0].gid == 9801
    assert (await send(a, chan=P.FRIEND_CHAN, arg=9801, content="an mich selbst")).err != 0
    assert (await send(a, chan=P.WORLD_CHAN, content="   ")).err != 0

    # b logs in again: conversation and world history are there
    b = await login(9802)
    info = P.ChatInfoS2C.FromString(b.last(chat.MSG_INFO))
    print("b's conversations:", [(f.friend_gid, [u.content for u in f.chat_list]) for f in info.chat_list])
    assert [u.content for u in info.chat_list[0].chat_list] == ["nur für dich"]
    assert info.recent_list[0].base.gid == 9801 and info.recent_list[0].state == P.ONLINE
    world = P.ChatUpdateS2C.FromString(b.last(chat.MSG_UPDATE))
    assert [u.content for u in world.world_msg_list] == ["Hallo zusammen"]

    await server.MESSAGE_HANDLERS[chat.MSG_READ](b, P.ChatFriendReadC2S(friend_gid=9801, read_time=123)
                                                 .SerializeToString())
    b = await login(9802)
    assert P.ChatInfoS2C.FromString(b.last(chat.MSG_INFO)).chat_list[0].read_time == 123

    await server.MESSAGE_HANDLERS[chat.MSG_START](b, P.ChatStartC2S(friend_gid=9801).SerializeToString())
    assert P.ChatStartS2C.FromString(b.last(chat.MSG_START)).recent_data.base.name == name_a

    # a goes offline: nothing is pushed to the closed connection any more
    del mail.ONLINE[9801]
    chat.on_logout(9801)
    await send(b, chan=P.FRIEND_CHAN, arg=9801, content="bist du da?")
    a2 = await login(9801)
    conv = P.ChatInfoS2C.FromString(a2.last(chat.MSG_INFO)).chat_list[0]
    assert [u.content for u in conv.chat_list] == ["nur für dich", "bist du da?"]
    del mail.ONLINE[9802]
    chat.on_logout(9802)
    a3 = await login(9801)
    rec = P.ChatInfoS2C.FromString(a3.last(chat.MSG_INFO)).recent_list[0]
    assert rec.state == P.OFFLINE and rec.last_logout > 1_700_000_000
    print("OK", TMP)


asyncio.run(main())
