"""
Time-limited profile unlocks (avatar frames, chat bubbles, titles) as Triple Clash season rewards.

The client keeps them in PersonalInfoS2C.item_ids (playerinfoitemconfig ids: 2xxx frames, 5xxx chat
bubbles, 6xxx titles) with PersonalInfoS2C.time_limit {item_id, times = expiry}; captured: the owner
had frame 2035 and title 6003 until the end of the season after ranking #34 (arenareward3v3
TimeLimitedReward [6003, 2035, 5028]). New unlocks go out as 0x2102 PersonalUpdateS2C (the full
item list plus the time limits), at once for online players and after every login.
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


def on_login(client) -> list[tuple[int, bytes]]:
    if not unlocks(client.gid):
        return []
    return [(MSG_UPDATE, update(client.gid, client.account))]
