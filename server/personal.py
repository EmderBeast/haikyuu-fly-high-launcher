"""
Time-limited profile unlocks (avatar frames, chat bubbles, titles) as Triple Clash season rewards.

The client keeps them in PersonalInfoS2C.item_ids (playerinfoitemconfig ids: 2xxx frames, 5xxx chat
bubbles, 6xxx titles) with PersonalInfoS2C.time_limit {item_id, times = expiry}; captured: the owner
had frame 2035 and title 6003 until the end of the season after ranking #34 (arenareward3v3
TimeLimitedReward [6003, 2035, 5028]). New unlocks go out as 0x2102 PersonalUpdateS2C (the full
item list plus the time limits), at once for online players and after every login.

Profile choices (title, portrait, frame, chat frame, banner, badges, bio, birthday, showcase
players: 0x2105-0x210C, 0x2111) were replayed from the capture before, so the game always switched
back to the owner's captured choice. They are stored per player here and sent again after login as
the same *SetS2C replies (the client's handlers simply take the value). A time-limited choice that
has expired is dropped, so the baseline value shows again.

Other players' profiles (0x210D PersonalOtherC2S{gid}, tapping a player in chat, friends, the club or
a ranking): built from that player's stored state, i.e. name / level from the arena snapshot every login
makes, the profile choices above over the account's baseline PersonalInfo, the showcase players as
PlayerCardInfo of the account's own cards, the club, arena and Triple Clash points. Unknown players
(never logged in here) get err 1. This replaced the captured reply, which showed a stranger.

Game settings (0x2402 SettingModifyC2S{setting_data: bits_data, bytes_data}, GAMER_SETTING_BIT): stored
per player and sent again after login as 0x2401 SettingInfoS2C (the captured reply answered every change
with the owner's bits). Others see them where the server decides: PersonalOtherS2C.setting (bit 3
PERSONAL_INFO_SHOW: the client greys out "personal info" without it), friend requests need bit 2
FRIEND_INVITE, club invites bit 4 CLUB_INVITE of the receiver (allows()).
"""
import time

import matches
import pvp

P = matches.P

MSG_UPDATE = 0x2102


def _store() -> dict:
    return pvp.state().setdefault("unlocks", {})


def unlocks(gid: int, now: float | None = None) -> dict[int, int]:
    """item id -> expiry (unix seconds) still valid for this player."""
    now = time.time() if now is None else now
    return {int(i): t for i, t in _store().get(str(gid), {}).items() if t > now}


def grant(gid: int, ids: list[int], until: int):
    """Unlock profile items until `until` (a later expiry wins over an earlier one)."""
    if not ids:
        return
    mine = _store().setdefault(str(gid), {})
    for i in ids:
        mine[str(i)] = max(int(mine.get(str(i), 0)), int(until))
    pvp.save()
    import mail
    client = mail.ONLINE.get(int(gid))
    if client is not None:
        import asyncio
        try:
            asyncio.get_running_loop().create_task(
                client.send_packet(MSG_UPDATE, update(gid, client.account)))
        except RuntimeError:
            pass


def update(gid: int, acc) -> bytes:
    base = []
    try:
        base = list(P.PersonalInfoS2C.FromString(acc.b.pb("0x2101")).item_ids)
    except Exception:
        pass
    extra = unlocks(gid)
    out = P.PersonalUpdateS2C()
    out.item_ids.extend(base + [i for i in extra if i not in base])
    for i, t in sorted(extra.items()):
        out.time_limit.add(item_id=i, times=t)
    return out.SerializeToString()


# --- profile choices ------------------------------------------------------------------------------
# msg id -> (C2S, S2C, field in both, repeated?)
SETTINGS = {
    0x2105: ("PersonalIconSetC2S", "PersonalIconSetS2C", "icon_id", False),
    0x2106: ("PersonalIconFrameSetC2S", "PersonalIconFrameSetS2C", "icon_frame_id", False),
    0x2107: ("PersonalSentenceSetC2S", "PersonalSentenceSetS2C", "sentence", False),
    0x2108: ("PersonalShowPlayerListSetC2S", "PersonalShowPlayerListSetS2C", "inst_ids", True),
    0x2109: ("PersonalBannerSetC2S", "PersonalBannerSetS2C", "banner_id", False),
    0x210A: ("PersonalBadgeSetC2S", "PersonalBadgeSetS2C", "conf_ids", True),
    0x210B: ("PersonalTitleSetC2S", "PersonalTitleSetS2C", "conf_id", False),
    0x210C: ("PersonalBirthSetC2S", "PersonalBirthSetS2C", "birth", False),
    0x2111: ("PersonalChatFrameSetC2S", "PersonalChatFrameSetS2C", "chat_frame_id", False),
}
TIMED = {0x2106, 0x210B, 0x2111}  # values that can be time-limited unlocks


def _choices(gid: int) -> dict:
    return pvp.state().setdefault("profile", {}).setdefault(str(gid), {})


def chosen(gid: int, msg_id: int, default=None):
    """The player's own choice for a setting (None/default = the account's baseline)."""
    v = pvp.state().get("profile", {}).get(str(gid), {}).get(f"{msg_id:04X}")
    if v is None or (msg_id in TIMED and v in _expired(gid)):
        return default
    return v


def _expired(gid: int) -> set[int]:
    now = time.time()
    return {int(i) for i, t in _store().get(str(gid), {}).items() if t <= now}


def _reply(msg_id: int, value) -> bytes:
    _, s2c, field, rep = SETTINGS[msg_id]
    out = getattr(P, s2c)()
    if rep:
        getattr(out, field).extend(value)
    else:
        setattr(out, field, value)
    return out.SerializeToString()


def _handler(msg_id: int):
    c2s, _, field, rep = SETTINGS[msg_id]

    async def handle(client, pb: bytes):
        value = getattr(getattr(P, c2s).FromString(pb), field)
        value = list(value) if rep else value
        _choices(client.gid)[f"{msg_id:04X}"] = value
        pvp.save()
        await client.send_packet(msg_id, _reply(msg_id, value))
    return handle


MSG_OTHER = 0x210D
MSG_SETTING_INFO, MSG_SETTING = 0x2401, 0x2402


def setting(gid: int, acc=None) -> P.SettingData:
    """The player's game settings: stored since a change, else the account's baseline."""
    v = _choices(gid).get("2402") if str(gid) in pvp.state().get("profile", {}) else None
    if v is not None:
        return P.SettingData(bits_data=v["bits"], bytes_data=bytes.fromhex(v["bytes"]))
    try:
        return P.SettingInfoS2C.FromString((acc or _account(gid)).b.pb("0x2401")).setting_data
    except Exception:
        return P.SettingData(bits_data=124)  # the baseline value: bits 2-6 on


def allows(gid: int, bit: int) -> bool:
    """GAMER_SETTING_BIT `bit` switched on by this player (e.g. FRIEND_INVITE, CLUB_INVITE)."""
    return bool(setting(gid).bits_data >> bit & 1)


async def handle_setting(client, pb: bytes):
    data = P.SettingModifyC2S.FromString(pb).setting_data
    _choices(client.gid)["2402"] = {"bits": data.bits_data, "bytes": data.bytes_data.hex()}
    pvp.save()
    out = P.SettingModifyS2C()
    out.setting_data.CopyFrom(data)
    await client.send_packet(MSG_SETTING, out.SerializeToString())


def _account(gid: int):
    """The player's account: the live one when online, else loaded from disk (not saved)."""
    import mail
    client = mail.ONLINE.get(gid)
    if client is not None:
        return client.account
    import accounts
    import starter
    return accounts.Account(gid, starter.baseline_for(gid))


def other(gid: int) -> P.PersonalOtherS2C:
    snap = pvp.state().get("snapshots", {}).get(str(gid))
    if not snap:
        return P.PersonalOtherS2C(err=1, gid=gid)
    acc = _account(gid)
    info = P.PersonalInfoS2C()
    try:
        info.ParseFromString(acc.b.pb("0x2101"))
    except Exception:
        pass
    out = P.PersonalOtherS2C(
        gid=gid, name=snap.get("name") or info.name, level=acc.level, exp=acc.exp, sex_type=info.sex_type,
        icon_id=chosen(gid, 0x2105, info.icon_id) or 0, icon_frame_id=chosen(gid, 0x2106, info.icon_frame_id) or 0,
        sentence=chosen(gid, 0x2107, info.sentence) or "", setting=setting(gid, acc).bits_data, show_banner_id=chosen(gid, 0x2109, info.show_banner_id) or 0,
        title_id=chosen(gid, 0x210B, info.title_id) or 0, birth=chosen(gid, 0x210C, info.birth) or "",
        arena_fight_score=pvp.state().get("arena", {}).get(str(gid), {}).get("score", 0),
        peak_score=pvp.state().get("peak", {}).get(str(gid), {}).get("score", 0))
    out.show_badge_ids.extend(chosen(gid, 0x210A, list(info.show_badge_ids)) or [])
    for inst in chosen(gid, 0x2108, list(info.show_player_inst_ids)) or []:
        raw = acc.get_card(inst) if inst else None
        if raw:
            out.show_players.append(matches.card_entry(P.PlayerCard.FromString(raw)))
    out.card_record_sum = len({P.PlayerCard.FromString(c).conf_id for c in _all_cards(acc)})
    import clubs
    c = clubs.club_of(gid)
    if c:
        out.association_id, out.association_name = c["id"], c["name"]
        out.position = c["members"][str(gid)]["pos"]
    return out


def _all_cards(acc) -> list[bytes]:
    own = {P.PlayerCard.FromString(c).inst_id: c for c in acc.cards}
    base = {i: c for i, c in acc.b.cards.items() if i not in own and i not in acc.removed_cards}
    return list(own.values()) + list(base.values())


async def handle_other(client, pb: bytes):
    gid = P.PersonalOtherC2S.FromString(pb).gid
    await client.send_packet(MSG_OTHER, other(gid).SerializeToString())


# --- wallpaper -------------------------------------------------------------------------------------
# 0x210F PersonalWallpaperSetC2S{item_id, set_type} / 0x2110 PersonalWallpaperEquipC2S{item_id}.
# Capture (decoded_tshark.json): set 1000001 type IMM -> S2C{item_id 1000001, set_type 2}; equip 1000001 ->
# S2C{item_id 1000001}, both echo the request. What the types mean is the client's own handling
# (PersonalInformationMessageHandler::OnPeronalWallPaperSet): ADD = into the random list and no fixed one,
# DEL = out of the random list, IMM = fixed wallpaper (0 = none) and the random list emptied; equip = the
# shown one (OnEquipWallPaper). The server keeps the same WallpaperSetInfo{fixed, random, equip} per player
# and sends it in PersonalInfoS2C.wallpaper_set (field 15) at login.
# Assumed: no ownership check, nothing else changes (no error case captured).
MSG_WALLPAPER_SET, MSG_WALLPAPER_EQUIP = 0x210F, 0x2110
W_ADD, W_DEL, W_IMM = 0, 1, 2


def wallpaper(gid: int, acc=None) -> "P.WallpaperSetInfo":
    out = P.WallpaperSetInfo()
    mine = pvp.state().get("profile", {}).get(str(gid), {}).get("wallpaper")
    if mine is not None:
        out.fixed, out.equip = mine.get("fixed", 0), mine.get("equip", 0)
        out.random.extend(mine.get("random", []))
    elif acc is not None:
        try:
            out.CopyFrom(P.PersonalInfoS2C.FromString(acc.b.pb("0x2101")).wallpaper_set)
        except Exception:
            pass
    return out


def _save_wallpaper(gid: int, w):
    _choices(gid)["wallpaper"] = {"fixed": w.fixed, "random": list(w.random), "equip": w.equip}
    pvp.save()


def info_message(gid: int, acc, body: bytes) -> bytes:
    """The baseline's PersonalInfoS2C with this player's wallpapers (field 15), everything else untouched."""
    w = wallpaper(gid, acc).SerializeToString()
    import wire
    return wire.build([x for x in wire.parse(body) if x[0] != 15] + [(15, 2, w)])


async def handle_wallpaper_set(client, pb: bytes):
    req = P.PersonalWallpaperSetC2S.FromString(pb)
    w = wallpaper(client.gid, client.account)
    if req.set_type == W_ADD:
        if req.item_id not in w.random:
            w.random.append(req.item_id)
        w.fixed = 0
    elif req.set_type == W_DEL:
        keep = [i for i in w.random if i != req.item_id]
        del w.random[:]
        w.random.extend(keep)
    else:
        del w.random[:]
        w.fixed = req.item_id
    _save_wallpaper(client.gid, w)
    await client.send_packet(MSG_WALLPAPER_SET, P.PersonalWallpaperSetS2C(item_id=req.item_id, set_type=req.set_type)
                             .SerializeToString())


async def handle_wallpaper_equip(client, pb: bytes):
    item = P.PersonalWallpaperEquipC2S.FromString(pb).item_id
    w = wallpaper(client.gid, client.account)
    w.equip = item
    _save_wallpaper(client.gid, w)
    await client.send_packet(MSG_WALLPAPER_EQUIP, P.PersonalWallpaperEquipS2C(item_id=item).SerializeToString())


HANDLERS = {m: _handler(m) for m in SETTINGS}
HANDLERS[MSG_WALLPAPER_SET] = handle_wallpaper_set
HANDLERS[MSG_WALLPAPER_EQUIP] = handle_wallpaper_equip
HANDLERS[MSG_OTHER] = handle_other
HANDLERS[MSG_SETTING] = handle_setting


def on_login(client) -> list[tuple[int, bytes]]:
    out = []
    if unlocks(client.gid):
        out.append((MSG_UPDATE, update(client.gid, client.account)))
    for m in SETTINGS:
        v = chosen(client.gid, m)
        if v is not None:
            out.append((m, _reply(m, v)))
    if "2402" in pvp.state().get("profile", {}).get(str(client.gid), {}):
        info = P.SettingInfoS2C()
        info.setting_data.CopyFrom(setting(client.gid, client.account))
        out.append((MSG_SETTING_INFO, info.SerializeToString()))
    return out
