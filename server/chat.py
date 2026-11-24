"""
Chat between the players of this server: world channel and private messages (0x1Bxx).

  0x1B01 ChatInfoS2C{chat_list, recent_list}   private conversations, sent after the login sync
  0x1B02 ChatUpdateS2C{chat_list, new_chat_person, world_msg_list, recent_data}   pushed
  0x1B03 ChatMsgSendC2S{chan, content, arg, emoji ...} -> ChatMsgSendS2C{err, chan}
  0x1B04 ChatFriendReadC2S{friend_gid, read_time}      private conversation read
  0x1B06 ChatStartC2S{friend_gid} -> ChatStartS2C{recent_data}   open a private conversation
  0x1B08 ChatCheckWordC2S{content} -> ChatCheckWordS2C{content}  (no word filter here)

The client shows a message's sender from ChatUpdateS2C.new_chat_person (FriendBase: name, icon,
frame, title; ChatUtils.GetChatChannelPlayerInfo). A world message goes to every online player
with the sender's FriendBase, the sender included: the client shows its own world message only when
the server sends it back (ChatInputViewLogic.OnSendButtonEvent adds it locally only for banned ad
accounts; ChatMsgUpdate filters own messages only with a chat setting on - checked in the
emulator). Private messages: ChatMsgSendC2S.arg = the receiver's gid; the sender gets it back too
(FriendChatData under the receiver's gid), its client does not add it locally either (emulator).
History (last WORLD_KEEP world messages, last PRIVATE_KEEP per conversation) is kept in pvp.json.
"""
import asyncio
import time

import matches
import mail
import pvp

P = matches.P

MSG_INFO, MSG_UPDATE, MSG_SEND, MSG_READ, MSG_START, MSG_CHECK = 0x1B01, 0x1B02, 0x1B03, 0x1B04, 0x1B06, 0x1B08
WORLD_KEEP, PRIVATE_KEEP, MAX_LEN = 50, 100, 200


def _state() -> dict:
    return pvp.state().setdefault("chat", {"world": [], "private": {}, "read": {}})


def _key(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


def base(gid: int) -> P.FriendBase:
    """Name / portrait / frame / title of a player, as other players see it."""
    import personal
    snap = pvp.state().get("snapshots", {}).get(str(gid), {})
    out = P.FriendBase(gid=gid, name=snap.get("name", ""), level=snap.get("level", 1),
                       icon=snap.get("icon", 0), icon_frame=snap.get("frame", 0),
                       title=personal.chosen(gid, 0x210B, 0) or 0)
    client = mail.ONLINE.get(gid)
    if client is None:
        out.logout_time = int(snap.get("time", 0))
    import clubs
    c = clubs.club_of(gid)
    if c:  # club name / role next to the player everywhere (chat, friends, member list)
        out.association_id, out.association_name = c["id"], c["name"]
        out.position = c["members"][str(gid)]["pos"]
    return out


def clubs_of(gid: int):
    import clubs
    return clubs.club_of(gid)


def _status(gid: int) -> int:
    return P.ONLINE if gid in mail.ONLINE else P.OFFLINE


def _recent(gid: int, last: int) -> P.RecentChatData:
    """last_logout (seconds) is what the contact list shows as "offline for ..." (GetPlayerStatus)."""
    seen = _state().get("logout", {}).get(str(gid)) or pvp.state().get("snapshots", {}).get(str(gid), {}).get("time", 0)
    return P.RecentChatData(base=base(gid), state=_status(gid), last_chat=last, last_logout=int(seen))


def on_logout(gid: int):
    _state().setdefault("logout", {})[str(gid)] = int(time.time())
    pvp.save()


def _unit(m: dict) -> P.ChatUnit:
    u = P.ChatUnit(gid=m["gid"], content=m["content"], time=m["time"], chat_frame_id=m.get("frame", 0),
                   quote_name=m.get("qn", ""), quote_content=m.get("qc", ""))
    if m.get("emoji"):
        u.args.add(type=P.CHAT_MSG_EMOJI, emoji=m["emoji"])
    return u


def _push(gid: int, upd: P.ChatUpdateS2C):
    client = mail.ONLINE.get(gid)
    if client is None:
        return
    try:
        asyncio.get_running_loop().create_task(client.send_packet(MSG_UPDATE, upd.SerializeToString()))
    except RuntimeError:
        pass


# --- login ------------------------------------------------------------------------------------------
def on_login(client) -> list[tuple[int, bytes]]:
    gid = int(client.gid)
    st = _state()
    info = P.ChatInfoS2C()
    persons = {}
    for key, msgs in st["private"].items():
        a, b = (int(x) for x in key.split("-"))
        if gid not in (a, b) or not msgs:
            continue
        other = b if a == gid else a
        fc = info.chat_list.add(friend_gid=other, read_time=st["read"].get(f"{gid}:{other}", 0))
        fc.chat_list.extend(_unit(m) for m in msgs)
        info.recent_list.append(_recent(other, msgs[-1]["time"]))
        persons[other] = True
    out = [(MSG_INFO, info.SerializeToString())]
    club = clubs_of(gid)
    club_msgs = club.get("chat", []) if club else []
    if st["world"] or club_msgs:
        upd = P.ChatUpdateS2C()
        upd.world_msg_list.extend(_unit(m) for m in st["world"])
        upd.association_msg_list.extend(_unit(m) for m in club_msgs)
        senders = {m["gid"] for m in st["world"] + club_msgs} - {gid}
        upd.new_chat_person.extend(base(g) for g in sorted(senders))
        out.append((MSG_UPDATE, upd.SerializeToString()))
    return out


# --- handlers ---------------------------------------------------------------------------------------
async def handle_send(client, pb: bytes):
    req = P.ChatMsgSendC2S.FromString(pb)
    gid = int(client.gid)
    import personal
    content = req.content[:MAX_LEN]
    if not content.strip() and not req.emoji:
        await client.send_packet(MSG_SEND, P.ChatMsgSendS2C(err=1, chan=req.chan).SerializeToString())
        return
    m = {"gid": gid, "content": content, "time": int(time.time()),
         "frame": personal.chosen(gid, 0x2111, 0) or 0, "emoji": req.emoji,
         "qn": req.quote_name, "qc": req.quote_content}
    st = _state()
    if req.chan == P.WORLD_CHAN:
        st["world"] = (st["world"] + [m])[-WORLD_KEEP:]
        upd = P.ChatUpdateS2C()
        upd.world_msg_list.append(_unit(m))
        upd.new_chat_person.append(base(gid))
        for other in list(mail.ONLINE):
            _push(other, upd)
    elif req.chan == P.FRIEND_CHAN and req.arg > 0 and req.arg != gid:
        to = int(req.arg)
        import friends
        if friends.is_blocked(to, gid):  # blocked by the receiver: accepted, but not delivered
            await client.send_packet(MSG_SEND, P.ChatMsgSendS2C(chan=req.chan).SerializeToString())
            return
        key = _key(gid, to)
        st["private"][key] = (st["private"].get(key, []) + [m])[-PRIVATE_KEEP:]
        upd = P.ChatUpdateS2C()
        upd.chat_list.add(friend_gid=gid).chat_list.append(_unit(m))
        upd.new_chat_person.append(base(gid))
        upd.recent_data.append(_recent(gid, m["time"]))
        _push(to, upd)
        echo = P.ChatUpdateS2C()
        echo.chat_list.add(friend_gid=to).chat_list.append(_unit(m))
        echo.recent_data.append(_recent(to, m["time"]))
        _push(gid, echo)
    elif req.chan == P.ASSOCIATION_CHAN and clubs_of(gid):
        c = clubs_of(gid)
        c["chat"] = (c.get("chat", []) + [m])[-WORLD_KEEP:]
        upd = P.ChatUpdateS2C()
        upd.association_msg_list.append(_unit(m))
        upd.new_chat_person.append(base(gid))
        for other in list(c["members"]):  # every online member, the sender too (like the world chat)
            _push(int(other), upd)
    else:  # club channel without a club
        await client.send_packet(MSG_SEND, P.ChatMsgSendS2C(err=1, chan=req.chan).SerializeToString())
        return
    pvp.save()
    await client.send_packet(MSG_SEND, P.ChatMsgSendS2C(chan=req.chan).SerializeToString())


async def handle_read(client, pb: bytes):
    req = P.ChatFriendReadC2S.FromString(pb)
    _state()["read"][f"{int(client.gid)}:{req.friend_gid}"] = req.read_time
    pvp.save()
    await client.send_packet(MSG_READ, P.ChatFriendReadS2C(friend_gid=req.friend_gid, read_time=req.read_time)
                             .SerializeToString())


async def handle_start(client, pb: bytes):
    other = P.ChatStartC2S.FromString(pb).friend_gid
    msgs = _state()["private"].get(_key(int(client.gid), other), [])
    out = P.ChatStartS2C(friend_gid=other)
    out.recent_data.CopyFrom(_recent(other, msgs[-1]["time"] if msgs else int(time.time())))
    await client.send_packet(MSG_START, out.SerializeToString())


async def handle_check(client, pb: bytes):
    content = P.ChatCheckWordC2S.FromString(pb).content
    await client.send_packet(MSG_CHECK, P.ChatCheckWordS2C(content=content).SerializeToString())


HANDLERS = {MSG_SEND: handle_send, MSG_READ: handle_read, MSG_START: handle_start, MSG_CHECK: handle_check}
