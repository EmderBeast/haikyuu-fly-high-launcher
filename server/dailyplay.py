"""
Daily events in "Freie Zeit" (client DailyPlay: 0x2D01 ShopEventInfoS2C, 0x2D02 ShopEventFinish), per player.

Before, the login replayed the owner's captured 0x2D01 (no events at all) and 0x2D02 was only confirmed:
nobody ever met an event.

Evidenced:
  - client (hotfix_il_full.txt, DailyPlay.*): 0x2D01 {1=all 2=repeated ShopEventUpdate{1=event_type
    2=current_count 3=season_time 4=repeated ShopEvent{1=event_id 2=deleted}}}; with `all` the client
    replaces its list, otherwise it adds / removes the events named; 0x2D02 C2S {1=event_id 2=is_win
    3=season_time (the type's season_time)} -> S2C {1=err}; the assistant (function 2) counts as done when
    current_count of types 1, 2 and 4 is 0 (AssistantUtils.CheckFunctionFinish)
  - tables: randomeventconfig (EventType, RefreshWeight, Win/Draw/DefeatReward [8, item, 16, number]),
    eventtypeconfig (RefreshTime hours, OneDayMaxNum)
  - own recording wcy2711 (27.11.2026, login 17:38 UTC): the login 0x2D01 (all) lists every type with
    season_time = the last RefreshTime hour (UTC) that has passed today (type 1: 06:00, type 2: 15:00, types
    3 / 4: 10:00), current_count = the plays left today (2, 2, 1, 2 = OneDayMaxNum) and one waiting event for
    types 1, 2 and 4 (none for type 3)
  - same recording and decrypted_1: the assistant plays current_count events of each of types 1, 2, 4 - the
    waiting one and new ones (wcy2711: 2 x type 1 with two different events, 2 x type 2, 2 x type 4) - and
    gets each one's WinReward, credited one by one; then 0x2D01 (not all) names the waiting events
    `deleted` with the type's season_time and no current_count; in the 0x3704 unit 2 every event's reward is
    listed on its own (401001536 x2, 401001112 x2, 102 x2000, 102 x2000, 131 x20, 131 x20)
Assumed (no capture of an event appearing or of 0x2D02):
  - at each RefreshTime hour one event of the type appears if none is waiting and plays are left; type 3
    (one row, never seen waiting) gets none
  - the plays start again with the daily missions' day (missions.periods)
  - 0x2D02: is_win -> WinReward, else DefeatReward (DrawReward is never chosen: the request has no draw);
    the rewards are credited without a popup, then 0x2D01 with the event deleted and the new count, then
    the reply; an unknown event or no plays left -> FAILED
State: pvp.json "dailyplay" {gid: {"day": ..., "types": {type: {"left", "season", "events": [ids]}}}}.
"""
import logging
import random
import time

import missions
import pvp

log = logging.getLogger("hfh")

MSG_INFO, MSG_FINISH = 0x2D01, 0x2D02
FAILED = 1
TYPES = {int(r["ID"]): r for r in pvp._cfg("eventtypeconfig")}
EVENTS = {int(r["ID"]): r for r in pvp._cfg("randomeventconfig")}
ASSIST_TYPES = (1, 2, 4)
NO_SPAWN = {3}


def _P():
    import matches
    return matches.P


def _hours(t: dict) -> list[int]:
    h = t.get("RefreshTime") or []
    return [x for x in (h if isinstance(h, list) else [h]) if isinstance(x, int) and 0 <= x < 24]


def last_slot(type_id: int, now: int) -> int:
    """The last RefreshTime hour (UTC) at or before now (yesterday's last one before today's first)."""
    day = now - now % 86400
    slots = sorted(day + h * 3600 for h in _hours(TYPES.get(type_id, {})))
    passed = [s for s in slots if s <= now]
    if passed:
        return passed[-1]
    return (slots[-1] - 86400) if slots else day


def reward(row: dict, key: str) -> list[tuple[int, int]]:
    v = row.get(key) or []
    tags = {v[i]: v[i + 1] for i in range(0, len(v) - 1, 2)} if isinstance(v, list) else {}
    return [(tags[8], tags.get(16, 1))] if 8 in tags else []


def pick(type_id: int, rng=random) -> int | None:
    rows = [(i, r.get("RefreshWeight") or 0) for i, r in EVENTS.items() if r.get("EventType") == type_id]
    rows = [(i, w) for i, w in rows if w > 0] or [(i, 1) for i, _ in rows]
    if not rows:
        return None
    return rng.choices([i for i, _ in rows], weights=[w for _, w in rows])[0]


def state(gid: int, now: int | None = None, rng=random) -> dict:
    """This player's events, brought up to now (new day: plays again; new slot: an event appears)."""
    now = int(time.time() if now is None else now)
    store = pvp.state().setdefault("dailyplay", {})
    d = store.setdefault(str(gid), {"day": "", "types": {}})
    day = missions.periods(now)["daily"]
    if d["day"] != day:
        d["day"] = day
        for t, row in TYPES.items():
            e = d["types"].setdefault(str(t), {"left": 0, "season": 0, "events": []})
            e["left"] = row.get("OneDayMaxNum") or 0
    for t in TYPES:
        e = d["types"].setdefault(str(t), {"left": 0, "season": 0, "events": []})
        slot = last_slot(t, now)
        if slot > e["season"]:
            e["season"] = slot
            if t not in NO_SPAWN and not e["events"] and e["left"] > 0:
                ev = pick(t, rng)
                if ev is not None:
                    e["events"].append(ev)
    return d


def info(gid: int, now: int | None = None) -> bytes:
    """ShopEventInfoS2C (all) for the login: every type with its plays left, season and waiting events."""
    P = _P()
    d = state(gid, now)
    pvp.save()
    out = P.ShopEventInfoS2C(all=True)
    for t in sorted(TYPES):
        e = d["types"][str(t)]
        u = out.events.add(event_type=t, current_count=e["left"], season_time=e["season"])
        for ev in e["events"]:
            u.event.add(event_id=ev)
    return out.SerializeToString()


def _update(changes: list[tuple[int, dict, list[int]]]) -> bytes:
    P = _P()
    out = P.ShopEventInfoS2C()
    for t, e, gone in changes:
        u = out.events.add(event_type=t, current_count=e["left"], season_time=e["season"])
        for ev in gone:
            u.event.add(event_id=ev, deleted=True)
    return out.SerializeToString()


async def handle_finish(client, pb: bytes):
    P = _P()
    req = P.ShopEventFinishC2S.FromString(pb)
    d = state(int(client.gid))
    row = EVENTS.get(req.event_id, {})
    t = row.get("EventType")
    e = d["types"].get(str(t)) if t is not None else None
    if e is None or req.event_id not in e["events"] or e["left"] <= 0:
        log.info(f"  DAILY event {req.event_id}: refused")
        await client.send_packet(MSG_FINISH, P.ShopEventFinishS2C(err=FAILED).SerializeToString())
        return
    e["events"].remove(req.event_id)
    e["left"] -= 1
    pvp.save()
    got = reward(row, "WinReward" if req.is_win else "DefeatReward")
    if got:
        await pvp.HOOKS["grant_rewards"](client, got, False)
    await client.send_packet(MSG_INFO, _update([(t, e, [req.event_id])]))
    await client.send_packet(MSG_FINISH, P.ShopEventFinishS2C().SerializeToString())
    log.info(f"  DAILY event {req.event_id} ({'won' if req.is_win else 'lost'}): {got}")


async def assistant(client, rng=random) -> list[tuple[int, int]]:
    """Assistant function 2: every play left of types 1, 2, 4 - waiting events first, then new ones -,
    each one's WinReward credited on its own; then 0x2D01 with the waiting events deleted."""
    d = state(int(client.gid), rng=rng)
    shown, changes = [], []
    for t in ASSIST_TYPES:
        e = d["types"].get(str(t))
        if not e or e["left"] <= 0:
            continue
        gone = list(e["events"])
        played = list(e["events"]) + [pick(t, rng) for _ in range(max(0, e["left"] - len(e["events"])))]
        for ev in played[:e["left"]]:
            got = reward(EVENTS.get(ev, {}), "WinReward")
            if got:
                await pvp.HOOKS["grant_rewards"](client, got, False)
                shown += got
        e["events"], e["left"] = [], 0
        changes.append((t, e, gone))
    if not changes:
        return []
    pvp.save()
    await client.send_packet(MSG_INFO, _update(changes))
    log.info(f"  ASSIST daily events: {shown}")
    return shown


HANDLERS = {MSG_FINISH: handle_finish}
