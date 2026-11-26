"""
Weekly activity "Bonus Training" (client: WeeklyActivities, 0x2F01 info, 0x2F02 train), per player.

Before, the login replayed the owner's captured 0x2F01 and 0x2F02 was answered from the capture.

Evidenced (weeklyactivity.json row 200 and four trainings in decoded_tshark.json, all ten-draws):
  - cost: TicketsID 201, OneDrawCost 1 / TenDrawCost 10 (0x2F02 is_train_one), taken before the rewards;
  - every draw gives BasePools (item 202 x 12; ten draws = 120 as captured);
  - a draw succeeds with Probability / 10000 (12 %): SuccessPools weighted [60, 20, 10, 9, 1] ->
    EXP 2000 / 5000 / 7000, item 102000009, item 102000010; else FailurePools (EXP 1000). Captured totals
    9000 + one 102000009, 16000, 20000, 16000 fit 9/1, 9/1, 8/2, 9/1 failures/successes;
  - special_train_count = draws left until a success is certain: SpecialCount (10) after a success, minus one
    per failure since (captured 10, 10, 8, 8); the draw at 1 left always succeeds;
  - train_count = draws this period (100 -> 110 -> 120 -> 130 -> 140); is_special = a success in this request;
  - missions of the activity's TaskType (14, 15; condition 20003 "Perform Bonus Training N Times") show
    train_count as progress (captured 110 / 120 / 130 right after the trainings);
  - the round missions (condition 99902 "Complete All Round N Missions", TastConditionTow = the round's 7 task
    ids) show how many of them are finished: in the same TaskChange that finished 14201, 15201 went to 1;
  - order: ticket item change, reward item/currency changes, TaskChange, then the 0x2F02 reply.
Assumed: the period (begin/end) repeats every DurationTime weeks from the captured one (9.11.2026 07:00,
2 weeks; the table has only activity 200); a new period starts at train_count 0, SpecialCount and the
activity's missions at 0. Token shop (ActivityShop 10611): tokens spent per shelf, see shop.py and spend().
State: pvp.json "weekly".
"""
import random
import time

import pvp
import wire

MSG_INFO, MSG_TRAIN, MSG_COST = 0x2F01, 0x2F02, 0x2F03
ROW = next(iter(pvp._cfg("weeklyactivity")), {})
CAPTURED_BEGIN = 1794204000  # 0x2F01 of the capture: 9.11.2026 07:00 (local), end 2 weeks later
PERIOD = (ROW.get("DurationTime") or 2) * 7 * 86400


def _pairs(spec) -> list:
    """[8, a, 16, b, 24, c] tag lists (nested first group allowed) -> [{8: a, 16: b, 24: c}, ...]."""
    flat = []
    for v in spec or []:
        flat.extend(v if isinstance(v, list) else [v])
    out, row = [], {}
    for i in range(0, len(flat) - 1, 2):
        tag, val = flat[i], flat[i + 1]
        if tag == 8 and row:
            out.append(row)
            row = {}
        row[tag] = val
    if row:
        out.append(row)
    return out


BASE = [(r[8], r[16]) for r in _pairs(ROW.get("BasePools"))]  # (item, number)
FAIL = [(r[16], r[24]) for r in _pairs(ROW.get("FailurePools"))]
SUCCESS = [(r[8], r[16], r[24]) for r in _pairs(ROW.get("SuccessPools"))]  # (weight, item, number)
TASK_TYPES = [v for k, v in sorted(_pairs(ROW.get("TaskType"))[0].items())] if ROW.get("TaskType") else []
TICKET = ROW.get("TicketsID", 201)
SPECIAL = ROW.get("SpecialCount", 10)
CHANCE = (ROW.get("Probability") or 0) / 10000


def period(now: float | None = None) -> tuple[int, int]:
    now = time.time() if now is None else now
    n = int((now - CAPTURED_BEGIN) // PERIOD)
    begin = CAPTURED_BEGIN + n * PERIOD
    return begin, begin + PERIOD


def state(gid: int, acc=None, now: float | None = None) -> dict:
    st = pvp.state().setdefault("weekly", {})
    begin, _ = period(now)
    d = st.get(str(gid))
    if d is None:
        d = st[str(gid)] = {"begin": 0, "count": 0, "special": SPECIAL}
        if acc is not None and begin == CAPTURED_BEGIN:  # the captured period: the baseline's own counts
            try:
                import matches
                cap = matches.P.WeeklyActivityInfoS2C.FromString(acc.b.pb("0x2F01"))
                d.update(begin=begin, count=cap.train_count, special=cap.special_train_count or SPECIAL,
                         shelves={str(c.shelves): c.integral_cost for c in cap.shelves_integral_cost})
            except Exception:
                pass
    if d["begin"] != begin:
        d.update(begin=begin, count=0, special=SPECIAL, reset_tasks=True, shelves={})
    return d


def info(gid: int, acc=None, now: float | None = None) -> bytes:
    import matches
    d = state(gid, acc, now)
    begin, end = period(now)
    out = matches.P.WeeklyActivityInfoS2C(weekly_activity_id=ROW.get("ID", 0), special_train_count=d["special"],
                                          train_count=d["count"], begin_unix=begin, end_unix=end)
    for shelf, cost in d.get("shelves", {}).items():  # tokens spent per shop shelf this period
        out.shelves_integral_cost.add(shelves=int(shelf), integral_cost=cost)
    return out.SerializeToString()


def spend(gid: int, acc, shelf: int, tokens: int) -> bytes:
    """Tokens spent in the activity's shop: 0x2F03 WeeklyActivityIntegralCostS2C for that shelf (capture:
    shelf 3, 900 -> 1350 after a 450-token buy)."""
    import matches
    d = state(gid, acc)
    shelves = d.setdefault("shelves", {})
    shelves[str(shelf)] = shelves.get(str(shelf), 0) + tokens
    pvp.save()
    return matches.P.WeeklyActivityIntegralCostS2C(shelves=shelf, integral_cost=shelves[str(shelf)]).SerializeToString()


def draw(d: dict, n: int, rng=random) -> tuple[list[tuple[int, int]], bool]:
    """n draws: rewards in Garena's order (base item, EXP, success items), summed per item; any success?"""
    totals: dict[int, int] = {}
    order: list[int] = []
    special = False

    def add(item, number):
        if item not in totals:
            order.append(item)
            totals[item] = 0
        totals[item] += number
    for _ in range(n):
        for item, number in BASE:
            add(item, number)
        if d["special"] <= 1 or rng.random() < CHANCE:
            _, item, number = rng.choices(SUCCESS, weights=[w for w, _, _ in SUCCESS])[0]
            d["special"] = SPECIAL
            special = True
        else:
            item, number = FAIL[0] if FAIL else (102, 0)
            d["special"] -= 1
        add(item, number)
    d["count"] += n
    base = [i for i, _ in BASE]
    rank = {i: (0 if i in base else 1 if i == (FAIL[0][0] if FAIL else 102) else 2) for i in order}
    order.sort(key=lambda i: rank[i])  # captured: 202, then EXP, then the success items
    return [(i, totals[i]) for i in order], special


ROUND_DONE = 99902  # TastConditionTow = task ids of the round, progress = how many of them are finished


def mission_progress(missions, count: int, reset: bool = False):
    """The activity's missions (TaskType 14 / 15, condition 20003) show the period's train count; the round
    missions (99902) count the finished missions of their round."""
    import gameconfig
    from accounts import TASK_FINISH
    for tid in missions.acc.all_task_ids():
        cfg = gameconfig.TASKS.get(tid, {})
        if cfg.get("TaskType") not in TASK_TYPES or cfg.get("TaskSubTargetType") != 20003:
            continue
        if reset:
            missions._set(tid, 0, 0)
            continue
        if missions.acc.task_state(tid) != 0:
            continue
        target = cfg.get("Value") or 1
        missions._set(tid, TASK_FINISH if count >= target else 0, min(count, target))
    for tid in missions.acc.all_task_ids():
        cfg = gameconfig.TASKS.get(tid, {})
        if cfg.get("TaskType") not in TASK_TYPES or cfg.get("TaskSubTargetType") != ROUND_DONE:
            continue
        if reset:
            missions._set(tid, 0, 0)
            continue
        if missions.acc.task_state(tid) != 0:
            continue
        done = sum(1 for t in cfg.get("TastConditionTow") or [] if missions.acc.task_state(t) != 0)
        old = wire.get(wire.parse(missions.acc.task(tid)), 3, 0)
        if done != old:
            target = cfg.get("Value") or 1
            missions._set(tid, TASK_FINISH if done >= target else 0, min(done, target))


async def handle_train(client, pb: bytes):
    import matches
    P = matches.P
    gid, acc = int(client.gid), client.account
    one = P.WeeklyActivityTrainC2S.FromString(pb).is_train_one
    n = 1 if one else 10
    cost = ROW.get("OneDrawCost", 1) if one else ROW.get("TenDrawCost", 10)
    d = state(gid, acc)
    begin, end = period()
    if not ROW or acc.item_count(TICKET) < cost or not begin <= time.time() < end:
        await client.send_packet(MSG_TRAIN, P.WeeklyActivityTrainS2C(err=1, special_train_count=d["special"],
                                                                     train_count=d["count"]).SerializeToString())
        return
    await client.send_packet(0x0802, wire.build([(1, 2, acc.set_item(TICKET, acc.item_count(TICKET) - cost))]))
    acc.save()
    rewards, special = draw(d, n)
    pvp.save()
    await pvp.HOOKS["grant_rewards"](client, rewards, False)
    m = getattr(client, "missions", None)
    if m is not None:
        if d.pop("reset_tasks", False):  # a new period: the activity's missions start again
            mission_progress(m, 0, reset=True)
        mission_progress(m, d["count"])
        pvp.save()
        if m.changed_tasks:
            m.save()
            acc.save()
            await client.send_packet(0x0E02, m.change_message())
    out = P.WeeklyActivityTrainS2C(special_train_count=d["special"], train_count=d["count"], is_special=special)
    for item, number in rewards:
        out.rewards.items.add(conf_id=item, number=number)
    await client.send_packet(MSG_TRAIN, out.SerializeToString())


HANDLERS = {MSG_TRAIN: handle_train}
