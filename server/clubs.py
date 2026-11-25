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
Club values are items for the client (Bag.BagUtils.GetItemCount): 128 = the member's club points
(AssociationNumber.gold), 129 = the club's funds (cur_funds), 130 = the member's contribution
(contribution this week, contribution_total overall). Rewards with them (daily welfare, weekly
benefits mail) are credited here, not to the inventory (credit(), called by server.grant_rewards).
Club funds: at most `association` DailyMaxFund a day (daliy_funds) and MaxFund in all; 0x2E0A levels
the club up for FundingConsume (leader / vice leader). Every week (weekly(), from seasons.run) each
member with contribution gets the associationbenefit row of the club level as mail 214 ("Weekly Club
Benefits"), then the week's contribution starts again. A kicked member gets mail 10.
Wishes and donations, club boss: see the sections below. Not built: buffet, icon shop, invite / recruit
messages (answered with an error). Rejoin cooldown: associationconstant 2 (24 h).
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
POINTS, FUNDS, CONTRIB = 128, 129, 130  # club items (BagUtils.GetItemCount)
BENEFITS = pvp._cfg("associationbenefit")
MAIL_WEEK, MAIL_KICKED = 214, 10
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
        cur_funds=c.get("funds", 0), total_funds=c.get("total_funds", 0),
        daliy_funds=c.get("day_funds", 0) if c.get("day") == pvp._today() else 0,
        review_condtion=c.get("cond", 0), cur_count=len(c["members"]), cr_time=c["created"],
        icon_id=c.get("icon", 0), cur_day_lv=c.get("lv", 1), cur_week_lv=c.get("lv", 1),
        cur_all_notify=len(c.get("notices", [])))


def _number(c: dict, gid: int) -> P.AssociationNumber:
    m = c["members"][str(gid)]
    daily = P.ASSOC_DAILY_ALREADY if m.get("daily") == pvp._today() else P.ASSOC_DAILY_REWARD
    return P.AssociationNumber(id=gid, association_id=c["id"], position=m["pos"], in_come_time=m["joined"],
                               update_time=m["joined"], daily_status=daily, read_notify=m.get("read", []),
                               gold=m.get("gold", 0), contribution=m.get("contrib", 0),
                               contribution_total=m.get("contrib_total", 0),
                               boss_score=m.get("boss", {}).get("week_total", 0)
                               if m.get("boss", {}).get("week") == _week() else 0)


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
    gid = int(client.gid)
    return [(MSG_INFO, info(gid)), (MSG_VOW_INFO, vow_info(club_of(gid))), (MSG_VOW_RECORD, vow_record(gid)),
            (MSG_BOSS_INFO, boss_info(gid))]


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


def credit(gid: int, conf: int, number: int) -> bool:
    """Credit a club item (128 / 129 / 130) to the player's club; False when it is no club item.
    Without a club the item is dropped (nothing to credit it to)."""
    if conf not in (POINTS, FUNDS, CONTRIB):
        return False
    c = club_of(gid)
    if c is None:
        return True
    m = c["members"][str(gid)]
    if conf == POINTS:
        m["gold"] = m.get("gold", 0) + number
    elif conf == CONTRIB:
        m["contrib"] = m.get("contrib", 0) + number
        m["contrib_total"] = m.get("contrib_total", 0) + number
    else:
        lv = _level(c)
        if c.get("day") != pvp._today():
            c["day"], c["day_funds"] = pvp._today(), 0
        room = min(lv.get("DailyMaxFund", 10 ** 9) - c["day_funds"], lv.get("MaxFund", 10 ** 9) - c.get("funds", 0))
        add = max(0, min(number, room))
        c["funds"] = c.get("funds", 0) + add
        c["total_funds"] = c.get("total_funds", 0) + add
        c["day_funds"] += add
    pvp.save()
    _refresh(c)
    return True


def _week(now: float | None = None) -> str:
    return time.strftime("%G-%V", time.localtime(time.time() if now is None else now))


def weekly(now: float | None = None) -> int:
    """New week: weekly benefits mail by last week's contribution, then the contribution starts again."""
    st = _st()
    week = _week(now)
    if st.get("week") == week:
        return 0
    first = "week" not in st
    st["week"] = week
    sent = 0
    for c in ([] if first else st["clubs"].values()):
        for g, m in c["members"].items():
            got = m.get("contrib", 0)
            row = next((r for r in BENEFITS if r.get("AssociationLevel") == c.get("lv", 1)
                        and r["Contribution"][0] <= got <= r["Contribution"][1]), None)
            if got > 0 and row:
                mail.send(int(g), mail.make(MAIL_WEEK, items=_kv(row.get("WeekReward")), args=[got], now=now))
                sent += 1
            m["contrib"] = 0
    pvp.save()
    return sent


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
        await client.send_packet(0x0802, wire.build([(1, 2, acc.set_item(cid, have - cost))]))
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
    mail.send(t, mail.make(MAIL_KICKED))
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


async def handle_level(client, pb: bytes):
    """0x2E0A: club level up for the level's FundingConsume (club funds), leader / vice leader."""
    c = _officer(client)
    cost = dict(_kv(_level(c).get("FundingConsume"))).get(FUNDS, 0) if c else 0
    if not c or c.get("lv", 1) + 1 not in LEVELS or c.get("funds", 0) < cost:
        await _reply(client, MSG_LV, "AssociationLvS2C", err=1)
        return
    c["funds"] -= cost
    c["lv"] = c.get("lv", 1) + 1
    pvp.save()
    await _reply(client, MSG_LV, "AssociationLvS2C")
    _refresh(c)


async def handle_unbuilt(client, pb: bytes, msg_id: int, cls: str):
    await _reply(client, msg_id, cls, err=1)


# --- wishes and donations (0x2E11-0x2E16) ------------------------------------------------------------
# A member wishes for shards of a player (piecemerge: player_id -> shard item), once a day
# (associationconstant 11). Other members donate one shard each (at most constant 10 = 3 a day); a
# wish takes at most GetMaxWishShardCount(shard rarity) = constants 12-16 (5/5/3/3/1), and the donor
# gets that constant's Data ("128_100,129_20,130_20": club points / funds / contribution). The wisher
# collects with 0x2E14; shards still uncollected when the wish ends (next day) come as mail 212.
# 0x2E11 AssociationVowInfoS2C{all, data: AssociationExtendVow} goes to every online member after a
# change; 0x2E12 AssociationVowRecordS2C{donate_record, donee_record, extend_data} to the player.
MSG_VOW_INFO, MSG_VOW_RECORD, MSG_THANK, MSG_RECEIVE, MSG_DONATE, MSG_WISH = range(0x2E11, 0x2E17)
PIECES = {r["ID"]: r["Pieceid"] for r in pvp._cfg("piecemerge")}
_ITEMS = {r["ID"]: r for r in pvp._cfg("item")}
WISHES_PER_DAY = _CONST.get(11, {}).get("Number", 1)
DONATES_PER_DAY = _CONST.get(10, {}).get("Number", 3)
MAIL_SHARDS = 212


def _rarity_row(player_id: int) -> dict:
    rare = _ITEMS.get(PIECES.get(player_id, 0), {}).get("Itemrare", 1)
    return _CONST.get(11 + max(1, min(5, rare)), {})


def _wish_max(player_id: int) -> int:
    return _rarity_row(player_id).get("Number", 1)


def _donor_reward(player_id: int) -> list[tuple[int, int]]:
    data = _rarity_row(player_id).get("Data") or ""
    return [tuple(int(x) for x in part.split("_")) for part in data.split(",") if "_" in part]


def _today_of(m: dict) -> dict:
    if m.get("vow_day") != pvp._today():
        m.update(vow_day=pvp._today(), wished=0, donated=0)
    return m


def _expire_wishes(c: dict):
    """Wishes of earlier days end; uncollected shards go to the wisher by mail."""
    keep = {}
    for g, w in c.setdefault("wishes", {}).items():
        if w["day"] == pvp._today():
            keep[g] = w
            continue
        left = w["got"] - w.get("taken", 0)
        if left > 0:
            mail.send(int(g), mail.make(MAIL_SHARDS, items=[(PIECES.get(w["player"], 0), left)]))
    c["wishes"] = keep


def _vow(g: str, w: dict) -> P.AssociationExtendVow:
    """donor_record = donations not collected yet (the client collects when it is not empty)."""
    return P.AssociationExtendVow(gid=int(g), player_id=w["player"], get_num=w["got"], vow_time=w["time"],
                                  donor_record=w.get("donors", [])[w.get("taken", 0):])


def vow_info(c: dict | None) -> bytes:
    out = P.AssociationVowInfoS2C(all=True)
    if c:
        _expire_wishes(c)
        out.data.extend(_vow(g, w) for g, w in c["wishes"].items())
    return out.SerializeToString()


def _record(r: dict) -> P.AssociationExtandVowRecord:
    b = base(r["gid"])
    return P.AssociationExtandVowRecord(inst_id=r["id"], gid=r["gid"], op_time=r["time"], is_thank=r.get("thank", False),
                                        icon=b.icon, icon_frame=b.icon_frame, name=b.name, chip_id=r["chip"], chip_num=1)


def vow_record(gid: int) -> bytes:
    c = club_of(gid)
    out = P.AssociationVowRecordS2C(all=True)
    if c:
        m = _today_of(c["members"][str(gid)])
        recs = c.setdefault("vow_records", [])
        out.donate_record.extend(_record(dict(r, gid=r["to"])) for r in recs if r["from"] == gid)
        out.donee_record.extend(_record(dict(r, gid=r["from"])) for r in recs if r["to"] == gid)
        out.extend_data.CopyFrom(P.AssociationExtendVowData(wish_num=m["wished"], donate_num=m["donated"],
                                                            last_player_id=m.get("last_wish", 0)))
    return out.SerializeToString()


def _vow_refresh(c: dict, also: tuple = ()):
    pb = vow_info(c)
    for g in list(c["members"]) + [str(x) for x in also]:
        _push(int(g), MSG_VOW_INFO, pb)
        _push(int(g), MSG_VOW_RECORD, vow_record(int(g)))


async def handle_wish(client, pb: bytes):
    gid = int(client.gid)
    pid = P.AssociationVowWishC2S.FromString(pb).player_id
    c = club_of(gid)
    m = _today_of(c["members"][str(gid)]) if c else None
    if not c or pid not in PIECES or m["wished"] >= WISHES_PER_DAY or str(gid) in c.get("wishes", {}):
        await _reply(client, MSG_WISH, "AssociationVowWishS2C", err=1)
        return
    _expire_wishes(c)
    c["wishes"][str(gid)] = {"player": pid, "got": 0, "taken": 0, "time": int(time.time()), "day": pvp._today(),
                             "donors": []}
    m["wished"] += 1
    m["last_wish"] = pid
    pvp.save()
    await _reply(client, MSG_WISH, "AssociationVowWishS2C")
    _vow_refresh(c)


async def handle_donate(client, pb: bytes):
    req = P.AssociationVowDonateC2S.FromString(pb)
    gid, acc = int(client.gid), client.account
    c = club_of(gid)
    w = c.get("wishes", {}).get(str(req.donee_id)) if c else None
    chip = PIECES.get(w["player"], 0) if w else 0
    m = _today_of(c["members"][str(gid)]) if c else None
    if (not w or req.donee_id == gid or w["day"] != pvp._today() or w["got"] >= _wish_max(w["player"])
            or m["donated"] >= DONATES_PER_DAY or acc.item_count(chip) < 1):
        await _reply(client, MSG_DONATE, "AssociationVowDonateS2C", err=1)
        return
    await client.send_packet(0x0802, wire.build([(1, 2, acc.set_item(chip, acc.item_count(chip) - 1))]))
    acc.save()
    rec = {"id": int(time.time() * 1000) * 1000 + len(c.setdefault("vow_records", [])) % 1000, "from": gid,
           "to": req.donee_id, "time": int(time.time()), "chip": chip}
    c["vow_records"] = (c["vow_records"] + [rec])[-200:]
    w["got"] += 1
    w["donors"].append(rec["id"])
    m["donated"] += 1
    pvp.save()
    await _reply(client, MSG_DONATE, "AssociationVowDonateS2C")
    await pvp.HOOKS["grant_rewards"](client, _donor_reward(w["player"]), True)
    _vow_refresh(c)


async def handle_receive(client, pb: bytes):
    """The client sends this whenever the wish view opens (AssociationShardViewLogic.CheckSelfHaveWish)
    and shows the reward view when data.donor_record is not empty: answer without an error and with
    only the donations not collected yet."""
    gid = int(client.gid)
    c = club_of(gid)
    w = c.get("wishes", {}).get(str(gid)) if c else None
    left = w["got"] - w.get("taken", 0) if w else 0
    if left <= 0:
        await _reply(client, MSG_RECEIVE, "AssociationVowReceiveS2C")
        return
    out = P.AssociationVowReceiveS2C()
    out.data.CopyFrom(_vow(str(gid), w))  # the new donations (shown in the reward view)
    w["taken"] = w["got"]
    pvp.save()
    await client.send_packet(MSG_RECEIVE, out.SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, [(PIECES[w["player"]], left)], True)
    _vow_refresh(c)


async def handle_thank(client, pb: bytes):
    gid = int(client.gid)
    inst = P.AssociationVowThankC2S.FromString(pb).inst_id
    c = club_of(gid)
    rec = next((r for r in c.get("vow_records", []) if r["id"] == inst and r["to"] == gid), None) if c else None
    if not rec or rec.get("thank"):
        await _reply(client, MSG_THANK, "AssociationVowThankS2C", err=1)
        return
    rec["thank"] = True
    pvp.save()
    await _reply(client, MSG_THANK, "AssociationVowThankS2C")
    _vow_refresh(c)


# --- club boss (0x2E1B / 0x2E1C, fights with FIGHT_ASSOC_BOSS) ----------------------------------------------
# The fight itself is a stage of the matches engine (stage row of associationbossstage[boss_id].StageID
# [stage_index], timed, power_hit_ball_data as the score display). Its score = the own team's summed attack
# and defence power of the match (what the client shows). Only the improvement of the player's best of
# the day counts for the club's week score (AssocBossCommon.current_score) and the player's week
# contribution - the client's quick-finish result says so ("Meine Kraft heute 2000 >> 8000, um 6000
# Punkte erhöht, ... zur Gesamtkraft des Clubs hinzugefügt"); best of the day / week / ever are kept
# (day_score, week_score, history_score). Rewards (AssociationBossViewUtils.GetRewardState): personal = rows of
# associationbosspersonal of the difficulty with PersonalScore <= day_score (claimed GroupIds in
# own_receive, new every day), club = associationbosswhole rows with AssociationScore <= current_score
# (claimed GroupIds in common_receive, new every week); 0x2E1C{reward_type 1 personal / 2 club} claims all
# reached ones. A new week: the difficulty goes up (NextHardID) when last week's club score reached the
# stage's Rank, the boss of the week is stage_index = ISO week % number of bosses.
# The boss view's member ranking (top 10, podium, own rank) is built by the client from the members'
# AssociationNumber.boss_score = the member's summed scores of the week ("Wettbewerbsbeitrag").
# Quick finish (PVPQuickBattleC2S, type 9, args [line-up, stage]; AssociationBossViewLogic.
# OnFightQuicklyBtnEvent): counts the week's best score as a fight without playing it (the client offers
# it only when today's best is below the week's best); the reply's battle_result.info.room_args.pvp_type
# must be 9 (AssociationMessageHandler.OnQuickBattleBack).
# Club ranking (rank view, clubs tab): RankListC2S rank_type 14 RANK_ASSOC_BOSS = the clubs' week score,
# rank_type 5 RANK_ASSOCIATION_TOTAL_FOUNDS = total funds; one RankListElem per club (gid = club id).
MSG_BOSS_INFO, MSG_BOSS_REWARD = 0x2E1B, 0x2E1C
BOSS_STAGES = {r["ID"]: r for r in pvp._cfg("associationbossstage")}
BOSS_PERSONAL = pvp._cfg("associationbosspersonal")
BOSS_WHOLE = pvp._cfg("associationbosswhole")
BOSS_FIGHTS_PER_DAY = 3


def _boss_club(c: dict, now: float | None = None) -> dict:
    b = c.setdefault("boss", {"week": "", "id": min(BOSS_STAGES) if BOSS_STAGES else 1, "score": 0})
    week = _week(now)
    if b["week"] != week:
        row = BOSS_STAGES.get(b["id"], {})
        if b["week"] and b["score"] >= row.get("Rank", 1 << 62) and row.get("NextHardID") in BOSS_STAGES:
            b["id"] = row["NextHardID"]
        b.update(week=week, score=0)
    return b


def _boss_me(c: dict, gid: int, now: float | None = None) -> dict:
    m = c["members"][str(gid)].setdefault("boss", {"day": "", "week": "", "history": 0})
    if m["day"] != pvp._today(now):
        m.update(day=pvp._today(now), day_score=0, day_power=0, fights=0, own=[])
    if m["week"] != _week(now):
        m.update(week=_week(now), week_score=0, week_total=0, common=[])
    return m


def boss_stage(c: dict) -> int:
    b = _boss_club(c)
    ids = BOSS_STAGES.get(b["id"], {}).get("StageID") or [0]
    return ids[int(_week().split("-")[1]) % len(ids)]


def boss_info(gid: int) -> bytes:
    c = club_of(gid)
    out = P.AssociationBossInfoS2C(all=True)
    if c is None:
        return out.SerializeToString()
    b, m = _boss_club(c), _boss_me(c, gid)
    ids = BOSS_STAGES.get(b["id"], {}).get("StageID") or [0]
    out.common.CopyFrom(P.AssocBossCommon(boss_id=b["id"], current_score=b["score"]))
    out.info.CopyFrom(P.AssocBossInfo(day_challenge_num=m["fights"], history_score=m["history"],
                                      day_score=m["day_score"], day_power=m["day_power"], own_receive=m["own"],
                                      common_receive=m["common"], week_score=m["week_score"]))
    out.stage_index = int(_week().split("-")[1]) % len(ids)
    return out.SerializeToString()


def _boss_refresh(c: dict):
    for g in c["members"]:
        _push(int(g), MSG_BOSS_INFO, boss_info(int(g)))


def boss_score(match) -> int:
    """The own team's summed attack + defence power of the match (power_hit_ball_data, ids 0-12)."""
    live = getattr(match, "live", None)
    tot = getattr(live, "power_tot", {}) if live else {}
    return int(sum(a + d for pid, (a, d) in tot.items() if 0 <= pid <= 12))


def boss_result(gid: int, match) -> int:
    c = club_of(gid)
    if c is None:
        return 0
    b, m = _boss_club(c), _boss_me(c, gid)
    score = boss_score(match)
    gain = max(0, score - m["day_score"])
    b["score"] += gain
    m["week_total"] = m.get("week_total", 0) + gain
    m["fights"] += 1
    if score > m["day_score"]:
        m["day_score"], m["day_power"] = score, int(getattr(match.info.home, "power", 0))
    m["week_score"] = max(m["week_score"], score)
    m["history"] = max(m["history"], score)
    pvp.save()
    _boss_refresh(c)
    _refresh(c)  # members' boss_score (the view's ranking)
    return score


class _Quick:
    """A finished boss fight without a match: the week's best score."""
    def __init__(self, score: int, power: int):
        self.live = type("L", (), {"power_tot": {0: [score, 0]}})()
        self.info = P.PVPRoomInfo()
        self.info.home.power = power


async def handle_boss_quick(client, req):
    gid = int(client.gid)
    c = club_of(gid)
    m = _boss_me(c, gid) if c else None
    out = P.PVPQuickBattleS2C()
    out.battle_result.info.room_args.CopyFrom(req.args)
    if not m or m["week_score"] <= m["day_score"]:
        out.error = 1
        await client.send_packet(0x0306, out.SerializeToString())
        return
    score = boss_result(gid, _Quick(m["week_score"], m.get("day_power", 0)))
    await client.send_packet(0x0306, out.SerializeToString())
    await client.send_packet(MSG_BOSS_INFO, boss_info(gid))
    return score


RANK_BOSS, RANK_FUNDS = P.RANK_ASSOC_BOSS, P.RANK_ASSOCIATION_TOTAL_FOUNDS


def _club_elem(c: dict, rank: int, kind: int) -> P.RankListElem:
    leader = next((int(g) for g, mm in c["members"].items() if mm["pos"] == LEADER), 0)
    value = _boss_club(c)["score"] if kind == RANK_BOSS else c.get("total_funds", 0)
    return P.RankListElem(gid=c["id"], icon=c.get("icon", 0), name=c["name"], level=c.get("lv", 1), rank=rank,
                          score=value, power=len(c["members"]), privilege_id=0,
                          title=base(leader).title if leader else 0)


def club_ranking(gid: int, kind: int) -> P.RankListData:
    def value(c):
        return _boss_club(c)["score"] if kind == RANK_BOSS else c.get("total_funds", 0)
    order = sorted(_st()["clubs"].values(), key=lambda c: (-value(c), c["id"]))
    order = [c for c in order if value(c) > 0] if kind == RANK_BOSS else order
    out = P.RankListData()
    out.rank.extend(_club_elem(c, i + 1, kind) for i, c in enumerate(order[:100]))
    mine = club_of(gid)
    if mine:
        place = next((i + 1 for i, c in enumerate(order) if c is mine), -1)
        out.own.CopyFrom(_club_elem(mine, place, kind))
    return out


async def handle_boss_reward(client, pb: bytes):
    gid = int(client.gid)
    kind = P.AssociationBossReceiveRewardC2S.FromString(pb).reward_type
    c = club_of(gid)
    if c is None:
        await _reply(client, MSG_BOSS_REWARD, "AssociationBossReceiveRewardS2C", err=1)
        return
    b, m = _boss_club(c), _boss_me(c, gid)
    if kind == 1:
        rows = [r for r in BOSS_PERSONAL if r.get("AssociationBossStageId") == b["id"]
                and r.get("PersonalScore", 0) <= m["day_score"] and r["GroupId"] not in m["own"]]
        got, items = m["own"], [i for r in rows for i in _kv(r.get("PersonalReward"))]
    else:
        rows = [r for r in BOSS_WHOLE if r.get("AssociationBossStageId") == b["id"]
                and r.get("AssociationScore", 0) <= b["score"] and r["GroupId"] not in m["common"]]
        got, items = m["common"], [i for r in rows for i in _kv(r.get("AssociationReward"))]
    if not rows:
        await _reply(client, MSG_BOSS_REWARD, "AssociationBossReceiveRewardS2C", err=1)
        return
    got.extend(r["GroupId"] for r in rows)
    pvp.save()
    await _reply(client, MSG_BOSS_REWARD, "AssociationBossReceiveRewardS2C")
    await client.send_packet(MSG_BOSS_INFO, boss_info(gid))
    merged: dict[int, int] = {}
    for conf, n in items:
        merged[conf] = merged.get(conf, 0) + n
    await pvp.HOOKS["grant_rewards"](client, list(merged.items()), True)


HANDLERS = {
    MSG_LIST: handle_list, MSG_SELECT: handle_select, MSG_CREATE: handle_create, MSG_APPLY: handle_apply,
    MSG_JOIN: handle_join, MSG_REVIEW: handle_review, MSG_APPOINT: handle_appoint, MSG_EXIT: handle_exit,
    MSG_DISBAND: handle_disband, MSG_DECL: handle_declaration, MSG_ICON: handle_icon, MSG_NAME: handle_name,
    MSG_NOTIFY: handle_notify, MSG_READ: handle_read, MSG_DAILY: handle_daily,
    MSG_LV: lambda c, pb: handle_level(c, pb),
    MSG_BOSS_REWARD: handle_boss_reward,
    MSG_WISH: handle_wish, MSG_DONATE: handle_donate, MSG_RECEIVE: handle_receive, MSG_THANK: handle_thank,
    MSG_INVITE: lambda c, pb: handle_unbuilt(c, pb, MSG_INVITE, "AssociationInviteS2C"),
    MSG_RECRUIT: lambda c, pb: handle_unbuilt(c, pb, MSG_RECRUIT, "AssociationRecruitS2C"),
}
