"""
Daily Lucky Draw (local activity type 11, ACTIVITY_DAY_TURNTABLE): spins and the cumulative (milestone) rewards,
per player.

Before, the login replayed the owner's captured activity (12 draws this week, milestones claimed) and
0x2809 / 0x280A had no handler: a spin used no ticket and gave nothing, the counter stayed at 12.

Evidenced, Garena capture captures/dizzy2711 (27.11.2026, dizzy2711.json, marks.txt):
  - spin = 0x2809 {} (client TurntableEverydayMessageHandler.ReqLocalActivityDayTurntableDo, 10249); the free
    spin first: 0x0405 {257 -> 0} (turntableconstantconf 9 = 257, the free-spin counter), else a ticket:
    0x0802 item 258 (constant 10) -1; then the prize as currency/item change (no pop-up), 0x2802 type-11 update
    (count +1, over_count +1, min_count = count, the prize appended to `log` (200 kept)), reply 0x2809 {id =
    turntableactivityconf row}. Captured: free spin -> row 9 (127 x500), ticket spin -> row 12 (102 x10000);
  - milestones = 0x280A {} (10250): every reached, unclaimed timerewardconfig row (Times <= over_count) at once
    (captured: 1 and 2 -> 100 x30000, 202000002 x10), item/currency changes, 0x0404 pop-up, 0x2802 with
    `rewards` = claimed row ids, reply 0x280A {};
  - prize weights: turntableactivityconf FreeProbability for the free spin, PayProbability for a ticket (the
    in-game chance list shows PayProbability / 100 %: 0.30 % row 1 ... 14 % rows 7-12);
  - rule text of the game (Pool Info, luckydraw_rules.png): the free spin resets to 1 daily at 06:00, the
    cumulative count and rewards every Monday 06:00, tickets do not reset; `free_time` = the next 06:00
    (captured 1795845600 = 28.11. 06:00 UTC), `reward_reset_time` = when this week's reset was applied
    (captured Mon 23.11. 16:17 UTC, after the 06:00 reset).
Assumed: 06:00 is UTC (as the other resets); no free spin and no ticket -> err FAILED; nothing reached ->
err FAILED. Not built: 0x280B (reset, no capture; the board has no reset button).
State: pvp.json "luckydraw" {gid: {day, week, count, over, rewards, log, reset_at}}.
"""
import random
import time

import pvp
import onlinereward

TYPE = 11
MSG_DO, MSG_COUNT, MSG_RESET = 0x2809, 0x280A, 0x280B
LOG_MAX = 200
_CONST = {r["ID"]: r.get("Value") for r in pvp._cfg("turntableconstantconf")}
FREE = int(_CONST.get(9) or 257)    # currency: free spins left today
TICKET = int(_CONST.get(10) or 258)  # item: Lucky Draw Ticket


def _P():
    import matches
    return matches.P


def _pair(spec):
    import weekly
    p = weekly._pairs(spec)
    return (p[0][8], p[0][16]) if p and 8 in p[0] and 16 in p[0] else None


PRIZES = {r["ID"]: r for r in pvp._cfg("turntableactivityconf")}
MILESTONES = {r["ID"]: r for r in pvp._cfg("timerewardconfig")}


def week_start(now: float) -> int:
    """The last Monday 06:00 UTC at or before now."""
    day = onlinereward.day_start(now)
    return day - time.gmtime(day).tm_wday * 86400


def baseline(acc):
    import wire
    P = _P()
    for m in acc.b.sync:
        if m["msg_id"].upper() == "0X2801":
            info = P.LocalActivityInfoS2C.FromString(wire.body_to_pb(bytes.fromhex(m["body_hex"]), m["flags"]))
            for a in info.activity_list:
                if a.type == TYPE:
                    return a, P.ActivityDayTurntable.FromString(a.data)
    return None, None


def state(gid: int, acc=None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    st = pvp.state().setdefault("luckydraw", {})
    d = st.get(str(gid))
    if d is None:
        d = st[str(gid)] = {"day": 0, "week": 0, "count": 0, "over": 0, "rewards": [], "log": [], "reset_at": 0}
        if acc is not None:
            _, base = baseline(acc)
            if base is not None:
                d["count"] = base.count
                d["log"] = [[l.id, l.count, l.time] for l in base.log][-LOG_MAX:]
    week = week_start(now)
    if d["week"] != week:  # Monday 06:00: the week's count and milestones start again
        d.update(week=week, over=0, rewards=[], reset_at=int(now))
    return d


def new_day(gid: int, acc, now: float | None = None) -> bool:
    """Daily 06:00: one free spin again (currency FREE = 1). Returns True when it was reset."""
    now = time.time() if now is None else now
    d = state(gid, acc, now)
    day = onlinereward.day_start(now)
    if d["day"] == day:
        return False
    d["day"] = day
    acc.set_currency(FREE, 1)
    acc.save()
    pvp.save()
    return True


def data(gid: int, acc, now: float | None = None) -> bytes:
    now = time.time() if now is None else now
    d = state(gid, acc, now)
    a, base = baseline(acc)
    out = _P().ActivityDayTurntable()
    if base is not None:
        out.CopyFrom(base)
    out.ClearField("log")
    out.ClearField("rewards")
    out.free_time = onlinereward.day_start(now) + 86400
    out.count = out.min_count = d["count"]
    out.over_count = d["over"]
    out.rewards.extend(d["rewards"])
    out.reward_reset_time = d["reset_at"]
    for item, n, t in d["log"][-LOG_MAX:]:
        out.log.add(id=item, count=n, time=t)
    return out.SerializeToString()


def patch_info(gid: int, acc, info) -> None:
    new_day(gid, acc)
    for a in info.activity_list:
        if a.type == TYPE:
            a.data = data(gid, acc)


def _start_time(acc) -> int:
    a, _ = baseline(acc)
    return a.start_time if a is not None else 0


def draw(free: bool, rng=random) -> int:
    key = "FreeProbability" if free else "PayProbability"
    ids = sorted(PRIZES)
    return rng.choices(ids, weights=[max(0, PRIZES[i].get(key) or 0) for i in ids])[0]


async def _update(client):
    P = _P()
    gid, acc = int(client.gid), client.account
    upd = P.LocalActivityUpdateS2C()
    upd.activity_list.add(type=TYPE, start_time=_start_time(acc), data=data(gid, acc))
    await client.send_packet(0x2802, upd.SerializeToString())


async def handle_do(client, pb: bytes):
    import wire
    from accounts import MSG_CURRENCY_CHANGE
    P = _P()
    gid, acc = int(client.gid), client.account
    new_day(gid, acc)
    d = state(gid, acc)
    free = acc.get_currency(FREE) > 0
    if not free and acc.item_count(TICKET) < 1:
        await client.send_packet(MSG_DO, P.LocalActivityDayTurntableDoS2C(err=P.FAILED).SerializeToString())
        return
    if free:
        await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, acc.set_currency(FREE, acc.get_currency(FREE) - 1))]))
    else:
        await client.send_packet(0x0802, wire.build([(1, 2, acc.set_item(TICKET, acc.item_count(TICKET) - 1))]))
    acc.save()
    row = draw(free)
    prize = _pair(PRIZES[row].get("GiftsId"))
    d["count"] += 1
    d["over"] += 1
    if prize:
        d["log"].append([prize[0], prize[1], int(time.time())])
        del d["log"][:-LOG_MAX]
    pvp.save()
    if prize:
        await pvp.HOOKS["grant_rewards"](client, [prize], False)
    await _update(client)
    await client.send_packet(MSG_DO, P.LocalActivityDayTurntableDoS2C(id=row).SerializeToString())


async def handle_count_reward(client, pb: bytes):
    P = _P()
    gid, acc = int(client.gid), client.account
    d = state(gid, acc)
    got = [i for i in sorted(MILESTONES) if i not in d["rewards"] and (MILESTONES[i].get("Times") or 0) <= d["over"]]
    if not got:
        await client.send_packet(MSG_COUNT, P.LocalActivityDayTurntableCountRewardS2C(err=P.FAILED).SerializeToString())
        return
    d["rewards"] += got
    pvp.save()
    import gameconfig
    rewards = [p for p in (_pair(MILESTONES[i].get("GiftsId")) for i in got) if p]
    await pvp.HOOKS["grant_rewards"](client, gameconfig.open_rewards(rewards), True)
    await _update(client)
    await client.send_packet(MSG_COUNT, P.LocalActivityDayTurntableCountRewardS2C().SerializeToString())


HANDLERS = {MSG_DO: handle_do, MSG_COUNT: handle_count_reward}
