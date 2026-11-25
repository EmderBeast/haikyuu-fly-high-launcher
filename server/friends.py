"""
Friends between the players of this server (0x1001-0x100C).

  0x1001 FriendInfoS2C          after the login sync: friends, requests, block list, suggestions,
                                friendship tokens sent today / waiting / collected today
  0x1002 FriendDataUpdateS2C    pushed: a friend added (NEW) or removed (DEL)
  0x1003 FriendInviteDataUpdateS2C  pushed: a new friend request
  0x1004 FriendSearchC2S{name}  -> FriendSearchS2C{data: FriendState}   by name (part) or gid
  0x1005 FriendRecommendUpdateC2S -> FriendRecommendUpdateS2C            random other players
  0x1006 FriendApplyC2S{apply_gid}                send a request
  0x1007 FriendApplyHandleC2S{AGREE/IGNORE, gids} answer requests
  0x1008 FriendRemoveC2S{gid}, 0x1009 FriendBlackHandleC2S{BLACK_ADD/DEL, gid}
  0x100A FriendshipSendC2S{friend_gid, send_all} -> FriendshipSendS2C{friendship_gids sent today}
  0x100B FriendshipGetC2S  collect what friends sent; 0x100C FriendshipUpdateS2C{wait_get, got}

Limits from the `friend` table (FriendsViewDef: row 1 friends 100, row 2 block list 100, row 5
suggestions 5, row 6 requests 100, row 9 friendship collected per day 20). Friendship collected =
Friendship Token (item 115), TOKENS_PER_GIFT each (row 10 = 5; the amount is not in the capture).
Blocking ends the friendship and stops private chat messages from that player (chat.py).
State in pvp.json ("friends").
"""
import asyncio
import random
import time

import matches
import mail
import pvp

P = matches.P

(MSG_INFO, MSG_UPDATE, MSG_INVITE, MSG_SEARCH, MSG_RECOMMEND, MSG_APPLY, MSG_HANDLE, MSG_REMOVE,
 MSG_BLACK, MSG_SEND, MSG_GET, MSG_COUNT) = range(0x1001, 0x100D)

_CFG = {r["ID"]: r.get("Intvalue", 0) for r in pvp._cfg("friend")}
MAX_FRIENDS, MAX_BLACK, RECOMMEND, MAX_REQUESTS, DAILY_GET = (
    _CFG.get(1, 100), _CFG.get(2, 100), _CFG.get(5, 5), _CFG.get(6, 100), _CFG.get(9, 20))
TOKEN, TOKENS_PER_GIFT = 115, _CFG.get(10, 5)


def _all() -> dict:
    return pvp.state().setdefault("friends", {})


def me(gid: int) -> dict:
    d = _all().setdefault(str(gid), {"friends": {}, "invites": {}, "black": {}, "sent": [], "wait": [],
                                     "got": 0, "day": ""})
    if d["day"] != pvp._today():
        d.update(day=pvp._today(), sent=[], got=0)
    return d


def _known(gid: int) -> bool:
    return str(gid) in pvp.state().get("snapshots", {})


def is_blocked(by: int, gid: int) -> bool:
    """True when `by` has put `gid` on the block list."""
    return str(gid) in _all().get(str(by), {}).get("black", {})


def _data(gid: int, since: int = 0) -> P.FriendData:
    import chat
    rec = chat._recent(gid, 0)
    return P.FriendData(base=rec.base, state=rec.state, last_logout=rec.last_logout, friend_start_time=since)


def _push(gid: int, msg_id: int, pb: bytes):
    client = mail.ONLINE.get(int(gid))
    if client is None:
        return
    try:
        asyncio.get_running_loop().create_task(client.send_packet(msg_id, pb))
    except RuntimeError:
        pass


def _count(gid: int) -> bytes:
    d = me(gid)
    return P.FriendshipUpdateS2C(friendship_wait_get=len(d["wait"]), friendship_got=d["got"]).SerializeToString()


def _recommend(gid: int) -> list:
    d = me(gid)
    skip = {str(gid)} | set(d["friends"]) | set(d["black"])
    pool = [int(g) for g in pvp.state().get("snapshots", {}) if g not in skip and int(g) > 0]
    online = [g for g in pool if g in mail.ONLINE]
    rest = [g for g in pool if g not in mail.ONLINE]
    random.shuffle(online)
    random.shuffle(rest)
    import chat
    return [chat.base(g) for g in (online + rest)[:RECOMMEND]]


def info(gid: int) -> bytes:
    import chat
    d = me(gid)
    out = P.FriendInfoS2C(friendship_wait_get=len(d["wait"]), friendship_got=d["got"])
    out.friend_list.extend(_data(int(g), t) for g, t in d["friends"].items())
    out.invite_list.extend(P.FriendInviteData(base=chat.base(int(g)), invite_time=t) for g, t in d["invites"].items())
    out.black_list.extend(P.FriendBlackData(friend=_data(int(g)), black_time=t) for g, t in d["black"].items())
    out.recommend_list.extend(_recommend(gid))
    out.friendship_gids.extend(int(g) for g in d["sent"])
    return out.SerializeToString()


def on_login(client) -> list[tuple[int, bytes]]:
    return [(MSG_INFO, info(int(client.gid)))]


def _befriend(a: int, b: int):
    now = int(time.time())
    for x, y in ((a, b), (b, a)):
        d = me(x)
        d["friends"][str(y)] = now
        d["invites"].pop(str(y), None)
        upd = P.FriendDataUpdateS2C(up_type=P.FRIEND_UPDATE_NEW)
        upd.friend_list.append(_data(y, now))
        _push(x, MSG_UPDATE, upd.SerializeToString())


def _unfriend(a: int, b: int):
    for x, y in ((a, b), (b, a)):
        d = me(x)
        if d["friends"].pop(str(y), None) is not None:
            upd = P.FriendDataUpdateS2C(up_type=P.FRIEND_UPDATE_DEL)
            upd.friend_list.append(P.FriendData(base=P.FriendBase(gid=y)))
            _push(x, MSG_UPDATE, upd.SerializeToString())
        if str(y) in d["wait"]:
            d["wait"].remove(str(y))


# --- handlers -------------------------------------------------------------------------------------------
async def handle_search(client, pb: bytes):
    import chat
    q = P.FriendSearchC2S.FromString(pb).name.strip()
    gid = int(client.gid)
    out = P.FriendSearchS2C()
    if q:
        hits = []
        for g, snap in pvp.state().get("snapshots", {}).items():
            if int(g) == gid or int(g) <= 0:
                continue
            if q == g or q.lower() in snap.get("name", "").lower():
                hits.append(int(g))
        for g in hits[:10]:
            out.data.add(data=chat.base(g), state=chat._status(g))
    await client.send_packet(MSG_SEARCH, out.SerializeToString())


async def handle_recommend(client, pb: bytes):
    out = P.FriendRecommendUpdateS2C()
    out.recommend_list.extend(_recommend(int(client.gid)))
    await client.send_packet(MSG_RECOMMEND, out.SerializeToString())


async def handle_apply(client, pb: bytes):
    import chat
    to = P.FriendApplyC2S.FromString(pb).apply_gid
    gid = int(client.gid)
    d, t = me(gid), me(to) if _known(to) else None
    err = 0
    import personal
    if t is None or to == gid or str(to) in d["friends"] or is_blocked(to, gid):
        err = 1
    elif not personal.allows(to, P.FRIEND_INVITE):  # the receiver switched friend requests off
        err = 1
    elif len(d["friends"]) >= MAX_FRIENDS or len(t["friends"]) >= MAX_FRIENDS or len(t["invites"]) >= MAX_REQUESTS:
        err = 1
    elif str(to) in d["invites"]:  # they asked first: become friends at once
        _befriend(gid, to)
    else:
        now = int(time.time())
        t["invites"][str(gid)] = now
        _push(to, MSG_INVITE, P.FriendInviteDataUpdateS2C(
            friend=P.FriendInviteData(base=chat.base(gid), invite_time=now)).SerializeToString())
    pvp.save()
    await client.send_packet(MSG_APPLY, P.FriendApplyS2C(apply_gid=to, err=err).SerializeToString())


async def handle_answer(client, pb: bytes):
    req = P.FriendApplyHandleC2S.FromString(pb)
    gid = int(client.gid)
    d = me(gid)
    gids = [g for g in (req.handle_gids or [int(x) for x in d["invites"]]) if str(g) in d["invites"]]
    done = []
    for g in gids:
        if req.handle_type == P.FRIEND_HANDLE_AGREE:
            if len(d["friends"]) >= MAX_FRIENDS or len(me(g)["friends"]) >= MAX_FRIENDS:
                continue
            _befriend(gid, g)
        else:
            d["invites"].pop(str(g), None)
        done.append(g)
    pvp.save()
    out = P.FriendApplyHandleS2C(handle_type=req.handle_type, err=0 if done else 1)
    out.handle_gid.extend(done)
    await client.send_packet(MSG_HANDLE, out.SerializeToString())


async def handle_remove(client, pb: bytes):
    other = P.FriendRemoveC2S.FromString(pb).gid
    ok = str(other) in me(int(client.gid))["friends"]
    _unfriend(int(client.gid), other)
    pvp.save()
    await client.send_packet(MSG_REMOVE, P.FriendRemoveS2C(gid=other, err=0 if ok else 1).SerializeToString())


async def handle_black(client, pb: bytes):
    req = P.FriendBlackHandleC2S.FromString(pb)
    gid = int(client.gid)
    d = me(gid)
    out = P.FriendBlackHandleS2C(handle_type=req.handle_type)
    if req.handle_type == P.BLACK_ADD:
        if not _known(req.gid) or req.gid == gid or len(d["black"]) >= MAX_BLACK:
            out.err = 1
        else:
            _unfriend(gid, req.gid)
            d["invites"].pop(str(req.gid), None)
            d["black"][str(req.gid)] = int(time.time())
            out.friend_black.CopyFrom(P.FriendBlackData(friend=_data(req.gid), black_time=d["black"][str(req.gid)]))
    else:
        t = d["black"].pop(str(req.gid), None)
        out.friend_black.CopyFrom(P.FriendBlackData(friend=_data(req.gid), black_time=t or 0))
        out.err = 0 if t else 1
    pvp.save()
    await client.send_packet(MSG_BLACK, out.SerializeToString())


async def handle_send(client, pb: bytes):
    req = P.FriendshipSendC2S.FromString(pb)
    gid = int(client.gid)
    d = me(gid)
    targets = list(d["friends"]) if req.send_all else [str(req.friend_gid)]
    for g in targets:
        if g in d["friends"] and g not in d["sent"]:
            d["sent"].append(g)
            t = me(int(g))
            if str(gid) not in t["wait"]:
                t["wait"].append(str(gid))
                _push(int(g), MSG_COUNT, _count(int(g)))
    pvp.save()
    out = P.FriendshipSendS2C()
    out.friendship_gids.extend(int(g) for g in d["sent"])
    await client.send_packet(MSG_SEND, out.SerializeToString())


async def handle_get(client, pb: bytes):
    gid = int(client.gid)
    d = me(gid)
    n = min(len(d["wait"]), max(0, DAILY_GET - d["got"]))
    d["wait"], d["got"] = d["wait"][n:], d["got"] + n
    pvp.save()
    await client.send_packet(MSG_GET, P.FriendshipGetS2C(err=0 if n else 1).SerializeToString())
    await client.send_packet(MSG_COUNT, _count(gid))
    if n:
        await pvp.HOOKS["grant_rewards"](client, [(TOKEN, n * TOKENS_PER_GIFT)], True)


HANDLERS = {MSG_SEARCH: handle_search, MSG_RECOMMEND: handle_recommend, MSG_APPLY: handle_apply,
            MSG_HANDLE: handle_answer, MSG_REMOVE: handle_remove, MSG_BLACK: handle_black,
            MSG_SEND: handle_send, MSG_GET: handle_get}
