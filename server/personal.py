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


HANDLERS = {m: _handler(m) for m in SETTINGS}


def on_login(client) -> list[tuple[int, bytes]]:
    out = []
    if unlocks(client.gid):
        out.append((MSG_UPDATE, update(client.gid, client.account)))
    for m in SETTINGS:
        v = chosen(client.gid, m)
        if v is not None:
            out.append((m, _reply(m, v)))
    return out
