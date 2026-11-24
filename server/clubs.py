"""
Clubs (Association, 0x2Exx) between the players of this server: the core of the club system.

  0x2E01 AssociationInfoS2C{all, association, numbers, self_number, reviews, association_ex}
         sent after the login sync and, after every change, to every online member (all = true:
         the client replaces its club data, OnReceiveAssociationInfo)
  0x2E02 list / 0x2E10 search (by ID or name) -> AssociationOrganization rows
  0x2E07 create {name, read_str = declaration, icon_id}: 300 diamonds (associationconstant 1 "108_300")
  0x2E08 apply {association_ids}: joins at once when the club is open (ASSOCIATION_ALLOW) and the
         level condition is met, otherwise the request waits for the leader / vice leaders
  0x2E04 answer requests {target_id, action}, 0x2E03 join condition {ALLOW / ALL_REVIEW, level}
  0x2E06 appoint {LEADER (hand over) / VICE_LEADER / NORMAL, terget_id} (association.VisePresident)
  0x2E0C exit {target_id}: own gid = leave (the leader hands over first, alone = disband), another
         member = kick (leader, vice leaders for normal members); the kicked player gets
         AssociationExitS2C with their own gid (client: AssociationUtils.KickOut)
  0x2E0B disband (leader): every member gets AssociationDisbandS2C
  0x2E09 declaration, 0x2E19 icon (free default icons), 0x2E23 name, 0x2E18 notice board
  (AssocNotify), 0x2E1A notice read, 0x2E17 daily welfare (association DailyReward of the level)
  Club chat: chat.py, ASSOCIATION_CHAN.
Not built: club level-up / funds, wishes and donations (0x2E11-0x2E16), boss, buffet, icon shop,
invite / recruit messages (answered with an error). Rejoin cooldown: associationconstant 2 (24 h).
Member limit: association PlayerCount of the level (level 1: 30). State in pvp.json ("clubs").
"""
import asyncio
import time

import matches
import mail
import pvp
import wire

P = matches.P

(MSG_INFO, MSG_LIST, MSG_REVIEW, MSG_JOIN, _, MSG_APPOINT, MSG_CREATE, MSG_APPLY, MSG_DECL, MSG_LV,
 MSG_DISBAND, MSG_EXIT, _, MSG_INVITE, MSG_RECRUIT, MSG_SELECT) = range(0x2E01, 0x2E11)
MSG_DAILY, MSG_NOTIFY, MSG_ICON, MSG_READ, MSG_NAME = 0x2E17, 0x2E18, 0x2E19, 0x2E1A, 0x2E23
MSG_CURRENCY_CHANGE = 0x0404

LEVELS = {r["ID"]: r for r in pvp._cfg("association")}
_CONST = {r["ID"]: r for r in pvp._cfg("associationconstant")}
CREATE_COST = tuple(int(x) for x in (_CONST.get(1, {}).get("Data") or "108_300").split("_"))
REJOIN_WAIT = _CONST.get(2, {}).get("Number", 86400)
FREE_ICONS = {r.get("ID", 0) for r in pvp._cfg("associationicon") if r.get("IsDefalt") == 1}
LEADER, VICE, NORMAL = P.ASSOCIATION_LEADER, P.ASSOCIATION_VICE_LEADER, P.ASSOCIATION_NORMAL
MAX_NAME, MAX_TEXT, LIST_SIZE = 14, 200, 20


def _st() -> dict:
    return pvp.state().setdefault("clubs", {"next": 10001, "clubs": {}, "member_of": {}, "left": {}})


def club_of(gid: int) -> dict | None:
    cid = _st()["member_of"].get(str(gid))
    return _st()["clubs"].get(str(cid)) if cid else None


def _level(c: dict) -> dict:
    return LEVELS.get(c.get("lv", 1), LEVELS.get(1, {}))


def _kv(spec) -> list[tuple[int, int]]:
    import mail as m
    return m.items_of(spec)


def _org(c: dict) -> P.AssociationOrganization:
    leader = next((int(g) for g, m in c["members"].items() if m["pos"] == LEADER), 0)
    import chat
    return P.AssociationOrganization(
        id=c["id"], lv=c.get("lv", 1), leader_id=leader, leader_name=chat.base(leader).name if leader else "",
        name=c["name"], declaration=c.get("decl", ""), review_status=c.get("review", P.ASSOCIATION_ALLOW),
        review_condtion=c.get("cond", 0), cur_count=len(c["members"]), cr_time=c["created"],
        icon_id=c.get("icon", 0), cur_day_lv=c.get("lv", 1), cur_week_lv=c.get("lv", 1),
        cur_all_notify=len(c.get("notices", [])))


def _number(c: dict, gid: int) -> P.AssociationNumber:
    m = c["members"][str(gid)]
    daily = P.ASSOC_DAILY_ALREADY if m.get("daily") == pvp._today() else P.ASSOC_DAILY_REWARD
    return P.AssociationNumber(id=gid, association_id=c["id"], position=m["pos"], in_come_time=m["joined"],
                               update_time=m["joined"], daily_status=daily, read_notify=m.get("read", []))


def base(gid: int):
    """FriendBase with the club (chat.base adds it)."""
    import chat
    return chat.base(gid)


def _ex(c: dict, gid: int) -> P.AssociationNumberEx:
    import chat
    return P.AssociationNumberEx(base=_number(c, gid), friend=base(gid), state=chat._status(gid))


def info(gid: int, joined: bool = False) -> bytes:
    """joined: ass_sysbol JOIN, the client's club list then opens the club view
    (AssociationListViewLogic.OnMessageEventChange)."""
    c = club_of(gid)
    out = P.AssociationInfoS2C(all=True, ass_sysbol=P.JOIN if joined else P.NORMAL)
    if c is None:
        out.self_number.CopyFrom(P.AssociationNumber(id=gid, exit_time=_st()["left"].get(str(gid), 0),
                                                     leave_time=_st()["left"].get(str(gid), 0)))
        return out.SerializeToString()
    out.association.CopyFrom(_org(c))
    out.numbers.extend(_ex(c, int(g)) for g in c["members"])
    out.self_number.CopyFrom(_number(c, gid))
    if c["members"][str(gid)]["pos"] in (LEADER, VICE):
        import chat
        for g, t in c.get("applies", {}).items():
            out.reviews.add(base=P.AssociationNumber(id=int(g)), friend=base(int(g)), state=chat._status(int(g)),
                            review_time=t)
    for n in c.get("notices", []):
        out.association_ex.notify.add(uid=n["uid"], title=n["title"], content=n["content"], name=n["name"],
                                      pisiton=n["pos"], crtime=n["time"])
    return out.SerializeToString()


def _push(gid: int, msg_id: int, pb: bytes):
    client = mail.ONLINE.get(int(gid))
    if client is None:
        return
    try:
        asyncio.get_running_loop().create_task(client.send_packet(msg_id, pb))
    except RuntimeError:
        pass


def _refresh(c: dict, also: list[int] = ()):
    """Full club state to every online member (and to `also`, e.g. someone who just left)."""
    for g in list(c["members"]) + [str(x) for x in also]:
        _push(int(g), MSG_INFO, info(int(g)))


def on_login(client) -> list[tuple[int, bytes]]:
    return [(MSG_INFO, info(int(client.gid)))]


def _add_member(c: dict, gid: int, pos=NORMAL):
    c["members"][str(gid)] = {"pos": pos, "joined": int(time.time())}
    c.get("applies", {}).pop(str(gid), None)
    _st()["member_of"][str(gid)] = c["id"]
    for other in _st()["clubs"].values():  # requests to other clubs are void now
        other.get("applies", {}).pop(str(gid), None)


def _remove_member(c: dict, gid: int):
    c["members"].pop(str(gid), None)
    _st()["member_of"].pop(str(gid), None)
    _st()["left"][str(gid)] = int(time.time())


def _full(c: dict) -> bool:
    return len(c["members"]) >= _level(c).get("PlayerCount", 30)


def _player_level(gid: int) -> int:
    return pvp.state().get("snapshots", {}).get(str(gid), {}).get("level", 1)


async def _reply(client, msg_id: int, cls, **kw):
    await client.send_packet(msg_id, getattr(P, cls)(**kw).SerializeToString())


# --- handlers -------------------------------------------------------------------------------------------
async def handle_list(client, pb: bytes):
    clubs = sorted(_st()["clubs"].values(), key=lambda c: (_full(c), -len(c["members"])))
    out = P.AssociationListS2C()
    out.association.extend(_org(c) for c in clubs[:LIST_SIZE])
    await client.send_packet(MSG_LIST, out.SerializeToString())


async def handle_select(client, pb: bytes):
    req = P.AssociationSelectC2S.FromString(pb)
    out = P.AssociationSelectS2C()
    for c in _st()["clubs"].values():
        if (req.condtion == P.ASSOCIATION_ID and (c["id"] == req.value or str(c["id"]) == req.name.strip())) or \
                (req.condtion != P.ASSOCIATION_ID and req.name.strip() and req.name.strip().lower() in c["name"].lower()):
            out.association.append(_org(c))
    await client.send_packet(MSG_SELECT, out.SerializeToString())


async def handle_create(client, pb: bytes):
    req = P.AssociationCreateC2S.FromString(pb)
    gid, acc = int(client.gid), client.account
    name = req.name.strip()[:MAX_NAME]
    cid, cost = CREATE_COST
    taken = any(c["name"].lower() == name.lower() for c in _st()["clubs"].values())
    have = acc.get_currency(cid) if acc.is_currency(cid) else acc.item_count(cid)
    if club_of(gid) or not name or taken or have < cost:
        await _reply(client, MSG_CREATE, "AssociationCreateS2C", err=1)
        return
    if acc.is_currency(cid):
        entry = acc.set_currency(cid, have - cost)
        await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, entry)]))
    else:
        await client.send_packet(0x0802, acc.set_item(cid, have - cost))
    acc.save()
    st = _st()
    c = {"id": st["next"], "name": name, "decl": req.read_str[:MAX_TEXT], "icon": req.icon_id if req.icon_id in FREE_ICONS else 0,
         "lv": 1, "created": int(time.time()), "review": P.ASSOCIATION_ALLOW, "cond": 0,
         "members": {}, "applies": {}, "notices": []}
    st["next"] += 1
    st["clubs"][str(c["id"])] = c
    _add_member(c, gid, LEADER)
    pvp.save()
    await _reply(client, MSG_CREATE, "AssociationCreateS2C")
    _refresh(c)


async def handle_apply(client, pb: bytes):
    gid = int(client.gid)
    ids = P.AssociationApplyC2S.FromString(pb).association_ids
    if club_of(gid) or time.time() - _st()["left"].get(str(gid), 0) < REJOIN_WAIT:
        await _reply(client, MSG_APPLY, "AssociationApplyS2C", err=1)
        return
    joined = False
    for cid in ids:
        c = _st()["clubs"].get(str(cid))
        if not c or _full(c) or _player_level(gid) < c.get("cond", 0):
            continue
        if c.get("review", P.ASSOCIATION_ALLOW) == P.ASSOCIATION_ALLOW:
            _add_member(c, gid)
            joined = True
            pvp.save()
            await _reply(client, MSG_APPLY, "AssociationApplyS2C", is_add=True)
            await client.send_packet(MSG_INFO, info(gid, joined=True))
            _refresh(c)
            return
        c.setdefault("applies", {})[str(gid)] = int(time.time())
        _refresh(c)
    pvp.save()
    await _reply(client, MSG_APPLY, "AssociationApplyS2C", is_add=joined)


async def handle_join(client, pb: bytes):
    """Leader / vice leader answers join requests."""
    req = P.AssociationJoinC2S.FromString(pb)
    gid = int(client.gid)
    c = club_of(gid)
    if not c or c["members"][str(gid)]["pos"] not in (LEADER, VICE):
        await _reply(client, MSG_JOIN, "AssociationJoinS2C", err=1)
        return
    targets = list(req.target_id) or [int(g) for g in c.get("applies", {})]
    for t in targets:
        if str(t) not in c.get("applies", {}):
            continue
        if req.action and not club_of(t) and not _full(c):
            _add_member(c, t)
            _push(t, MSG_INFO, info(t, joined=True))
        else:
            c["applies"].pop(str(t), None)
    pvp.save()
    await _reply(client, MSG_JOIN, "AssociationJoinS2C")
    _refresh(c)


async def handle_review(client, pb: bytes):
    req = P.AssociationReviewC2S.FromString(pb)
    c = club_of(int(client.gid))
    if not c or c["members"][str(client.gid)]["pos"] not in (LEADER, VICE):
        await _reply(client, MSG_REVIEW, "AssociationReviewS2C", err=1)
        return
    c["review"], c["cond"] = req.condition, max(0, req.value)
    pvp.save()
    await _reply(client, MSG_REVIEW, "AssociationReviewS2C")
    _refresh(c)


async def handle_appoint(client, pb: bytes):
    req = P.AssociationAppointC2S.FromString(pb)
    gid, t = int(client.gid), req.terget_id
    c = club_of(gid)
    ok = c and c["members"][str(gid)]["pos"] == LEADER and str(t) in c["members"] and t != gid
    if ok and req.appoint == VICE:
        vices = sum(1 for m in c["members"].values() if m["pos"] == VICE)
        ok = vices < _level(c).get("VisePresident", 2)
    if not ok:
        await _reply(client, MSG_APPOINT, "AssociationAppointS2C", err=1)
        return
    if req.appoint == LEADER:
        c["members"][str(gid)]["pos"] = NORMAL
    c["members"][str(t)]["pos"] = req.appoint
    pvp.save()
    await _reply(client, MSG_APPOINT, "AssociationAppointS2C")
    _refresh(c)


def _successor(c: dict, leaving: int) -> str | None:
    rest = [(m["pos"], m["joined"], g) for g, m in c["members"].items() if g != str(leaving)]
    return min(rest)[2] if rest else None


def _disband(c: dict):
    members = list(c["members"])
    for g in members:
        _remove_member(c, int(g))
        _push(int(g), MSG_DISBAND, P.AssociationDisbandS2C().SerializeToString())
        _push(int(g), MSG_INFO, info(int(g)))
    _st()["clubs"].pop(str(c["id"]), None)


async def handle_exit(client, pb: bytes):
    gid = int(client.gid)
    t = P.AssociationExitC2S.FromString(pb).target_id or gid
    c = club_of(gid)
    if not c or str(t) not in c["members"]:
        await _reply(client, MSG_EXIT, "AssociationExitS2C", err=1, target_id=t)
        return
    if t == gid:  # leave
        if c["members"][str(gid)]["pos"] == LEADER:
            nxt = _successor(c, gid)
            if nxt is None:
                _disband(c)
                pvp.save()
                await _reply(client, MSG_EXIT, "AssociationExitS2C", target_id=t, is_exit=True)
                return
            c["members"][nxt]["pos"] = LEADER
        _remove_member(c, gid)
        pvp.save()
        await _reply(client, MSG_EXIT, "AssociationExitS2C", target_id=t, is_exit=True)
        await client.send_packet(MSG_INFO, info(gid))
        _refresh(c)
        return
    mine, theirs = c["members"][str(gid)]["pos"], c["members"][str(t)]["pos"]
    if not (mine == LEADER or (mine == VICE and theirs == NORMAL)):
        await _reply(client, MSG_EXIT, "AssociationExitS2C", err=1, target_id=t)
        return
    _remove_member(c, t)
    pvp.save()
    await _reply(client, MSG_EXIT, "AssociationExitS2C", target_id=t)
    _push(t, MSG_EXIT, P.AssociationExitS2C(target_id=t).SerializeToString())  # kicked: KickOut
    _push(t, MSG_INFO, info(t))
    _refresh(c)


async def handle_disband(client, pb: bytes):
    c = club_of(int(client.gid))
    if not c or c["members"][str(client.gid)]["pos"] != LEADER:
        await _reply(client, MSG_DISBAND, "AssociationDisbandS2C", err=1)
        return
    _disband(c)
    pvp.save()


def _officer(client) -> dict | None:
    c = club_of(int(client.gid))
    return c if c and c["members"][str(client.gid)]["pos"] in (LEADER, VICE) else None


async def handle_declaration(client, pb: bytes):
    c = _officer(client)
    if c:
        c["decl"] = P.AssociationDeclarationC2S.FromString(pb).read_str[:MAX_TEXT]
        pvp.save()
        _refresh(c)
    await _reply(client, MSG_DECL, "AssociationDeclarationS2C", err=0 if c else 1)


async def handle_icon(client, pb: bytes):
    c = _officer(client)
    icon = P.AssociationIconC2S.FromString(pb).icon_id
    ok = c is not None and icon in FREE_ICONS
    if ok:
        c["icon"] = icon
        pvp.save()
        _refresh(c)
    await _reply(client, MSG_ICON, "AssociationIconS2C", err=0 if ok else 1)


async def handle_name(client, pb: bytes):
    c = club_of(int(client.gid))
    name = P.AssociationModifyNameC2S.FromString(pb).name.strip()[:MAX_NAME]
    ok = (c is not None and c["members"][str(client.gid)]["pos"] == LEADER and name
          and not any(o["name"].lower() == name.lower() for o in _st()["clubs"].values() if o is not c))
    if ok:
        c["name"] = name
        pvp.save()
        _refresh(c)
    await _reply(client, MSG_NAME, "AssociationModifyNameS2C", err=0 if ok else 1)


async def handle_notify(client, pb: bytes):
    req = P.AssociationNotifyC2S.FromString(pb)
    c = _officer(client)
    if c:
        import chat
        notes = c.setdefault("notices", [])
        uid = max([n["uid"] for n in notes] + [0]) + 1
        notes.append({"uid": uid, "title": req.title[:40], "content": req.content[:MAX_TEXT],
                      "name": chat.base(int(client.gid)).name, "pos": c["members"][str(client.gid)]["pos"],
                      "time": int(time.time())})
        del notes[:-10]
        pvp.save()
        _refresh(c)
    await _reply(client, MSG_NOTIFY, "AssociationNotifyS2C", err=0 if c else 1)


async def handle_read(client, pb: bytes):
    c = club_of(int(client.gid))
    rid = P.AssociationReadC2S.FromString(pb).read_id
    if c:
        read = c["members"][str(client.gid)].setdefault("read", [])
        if rid not in read:
            read.append(rid)
        pvp.save()
    await _reply(client, MSG_READ, "AssociationReadS2C", err=0 if c else 1)


async def handle_daily(client, pb: bytes):
    c = club_of(int(client.gid))
    m = c["members"][str(client.gid)] if c else None
    if not m or m.get("daily") == pvp._today():
        await _reply(client, MSG_DAILY, "AssociationDailyFreeS2C", err=1)
        return
    m["daily"] = pvp._today()
    pvp.save()
    await _reply(client, MSG_DAILY, "AssociationDailyFreeS2C")
    await client.send_packet(MSG_INFO, info(int(client.gid)))
    await pvp.HOOKS["grant_rewards"](client, _kv(_level(c).get("DailyReward")), True)


async def handle_unbuilt(client, pb: bytes, msg_id: int, cls: str):
    await _reply(client, msg_id, cls, err=1)


HANDLERS = {
    MSG_LIST: handle_list, MSG_SELECT: handle_select, MSG_CREATE: handle_create, MSG_APPLY: handle_apply,
    MSG_JOIN: handle_join, MSG_REVIEW: handle_review, MSG_APPOINT: handle_appoint, MSG_EXIT: handle_exit,
    MSG_DISBAND: handle_disband, MSG_DECL: handle_declaration, MSG_ICON: handle_icon, MSG_NAME: handle_name,
    MSG_NOTIFY: handle_notify, MSG_READ: handle_read, MSG_DAILY: handle_daily,
    MSG_LV: lambda c, pb: handle_unbuilt(c, pb, MSG_LV, "AssociationLvS2C"),
    MSG_INVITE: lambda c, pb: handle_unbuilt(c, pb, MSG_INVITE, "AssociationInviteS2C"),
    MSG_RECRUIT: lambda c, pb: handle_unbuilt(c, pb, MSG_RECRUIT, "AssociationRecruitS2C"),
}
