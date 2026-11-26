"""
High School Trials: buying the daily reward again (0x5603 HighSchoolTrialsDailyRewardsBuyC2S{num}). Before,
the request got the captured empty reply: nothing paid, nothing given.

Evidenced (decoded_tshark.json, one buy with num 1; tables highschooldailyconf / highschoolconstantconf, item,
gift, coachlist, coachlevel; the account's CoachInfoS2C.setting):
  - cost: highschoolconstantconf 3 "108_100,108_150" - the first buy 100 diamonds (10045 -> 9945), sent as
    0x0405, then the spend mission (0x0E02, 6000003 +100);
  - 0x5601 HighSchoolTrialsInfoS2C with only `base`: daily_reward_buy_num + 1, the rest unchanged;
  - reward = highschooldailyconf[base.daily_reward_id] (17 for daily_stage_num 780), every entry
    "item_number_chance" with chance / 10000 (100 x245000 as in the table);
  - the coach boxes 102000018 / 19 / 20 (item Useway 4 -> gift Randomreward) open into one random coach each
    (coach items 1101001xx, Droptype 8, Itemresult = coachlist id); coaches up to the player's
    CoachCardSetting.auto_resolve_rare (captured account: PURPLE = 3) are dismantled at once: 336 +=
    coachlevel[rarity][1].Resolve (3 x 480 + 3 x 800 = 3840; 49000 + 3840 = 52840 as captured) and listed in
    NotifyRewardsS2C.items_decomp; the others become coach cards (level 1, 0x5502 each);
  - order: item changes (0x0802), currencies (0x0405), 0x5502 per new coach, the pop-up, achievement 91201
    [rarity] +1 per kept coach of that rarity (8401504 [4]: 964 -> 966), then the empty 0x5603 reply.
Assumed: at most highschoolconstantconf 2 (10) buys a day, the price list's last entry for every further buy;
the buy counter resets at 06:00 (constant 1) of the game day; too little to pay or over the limit -> error 1.
Free daily reward (0x5602) and stage rewards (0x5604), from the client's code and the tables (no capture; before,
both were only confirmed and nothing was given):
  - HighSchoolTrialsBase.daily_reward_state: false = the free reward can be taken (SchoolStageData::UpdateRedPoint
    shows the red point then), true = taken. The reward is the same highschooldailyconf row the buy pays
    (daily_reward_id), the buy being "the daily reward again";
  - stage rewards: highschoolrewardconf rows with cumulative_reward_id < AwardConditions <= stages passed, where
    stages passed = sum of high_school_stage_id % 1000 over the schools (SchoolStageData::GetTotalStage). The
    request has no fields. Capture: six schools at stage 130 = 780, cumulative_reward_id 780 (all taken),
    daily_reward_state true;
  - both replies carry only err; the state reaches the client as HighSchoolTrialsInfoS2C.base, as captured for
    the buy.
  Assumed: order base, rewards, reply as for the buy; the free reward comes back with the buy counter at the
  daily reset; one 0x5604 takes every reward reached; nothing to take -> error 1.
Not built: the stages themselves (progress only from the baseline), auto_resolve_confids.
State: pvp.json "hstrials" {gid: {"buys": n, "reset": unix, "daily": bool, "cum": id}}.
"""
import random
import time

import pvp
import wire

MSG_INFO, MSG_DAILY, MSG_BUY, MSG_TOTAL = 0x5601, 0x5602, 0x5603, 0x5604
CONST = {r["ID"]: r for r in pvp._cfg("highschoolconstantconf")}
DAILY = {r["ID"]: r for r in pvp._cfg("highschooldailyconf")}
STAGE_REWARDS = {r["ID"]: r for r in pvp._cfg("highschoolrewardconf")}
MAX_BUYS = CONST.get(2, {}).get("Value1") or 10
PRICES = [tuple(int(x) for x in p.split("_")) for p in str(CONST.get(3, {}).get("Value2") or "").split(",") if "_" in p]
ITEMS = {r["ID"]: r for r in pvp._cfg("item")}
GIFTS = {r["ID"]: r for r in pvp._cfg("gift")}
COACH_RESOLVE = 336
COACH_GET_ACH = 91201  # TastConditionTow = coach rarity
DAY = 86400


def _P():
    import matches
    return matches.P


def _base(acc):
    try:
        return _P().HighSchoolTrialsInfoS2C.FromString(acc.b.pb("0x5601")).base
    except Exception:
        return _P().HighSchoolTrialsBase()


def _state(gid: int, acc, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    st = pvp.state().setdefault("hstrials", {}).setdefault(str(gid), {"buys": None, "reset": 0})
    if st["reset"] and now >= st["reset"]:
        st["buys"], st["reset"], st["daily"] = 0, 0, False
    if not st["reset"]:
        nxt = _base(acc).daily_reset_next_unix or 0
        while nxt and nxt <= now:
            nxt += DAY
        st["reset"] = nxt or int(now) + DAY
    if st["buys"] is None:
        st["buys"] = _base(acc).daily_reward_buy_num
    return st


def total_stages(acc) -> int:
    """Stages passed over all schools: sum of stage id % 1000 (SchoolStageData::GetTotalStage)."""
    try:
        return sum(p.high_school_stage_id % 1000 for p in _P().HighSchoolTrialsInfoS2C.FromString(acc.b.pb("0x5601")).proresses)
    except Exception:
        return 0


def stage_rewards(row: dict) -> list[tuple[int, int]]:
    """highschoolrewardconf.Reward: [[8, item, 16, number], 8, item, 16, number, ...]."""
    flat = []
    for x in row.get("Reward") or []:
        flat += x if isinstance(x, list) else [x]
    return [(flat[i + 1], flat[i + 3]) for i in range(0, len(flat) - 3, 4) if flat[i] == 8 and flat[i + 2] == 16]


def base(gid: int, acc, now: float | None = None):
    st = _state(gid, acc, now)
    b = _P().HighSchoolTrialsBase()
    b.CopyFrom(_base(acc))
    b.daily_reward_buy_num, b.daily_reset_next_unix = st["buys"], st["reset"]
    if st.get("daily") is not None:
        b.daily_reward_state = st["daily"]
    if st.get("cum") is not None:
        b.cumulative_reward_id = st["cum"]
    return b


def info(gid: int, acc) -> bytes:
    """The baseline's HighSchoolTrialsInfoS2C with this player's buy counter."""
    P = _P()
    out = P.HighSchoolTrialsInfoS2C()
    try:
        out.CopyFrom(P.HighSchoolTrialsInfoS2C.FromString(acc.b.pb("0x5601")))
    except Exception:
        pass
    out.base.CopyFrom(base(gid, acc))
    return out.SerializeToString()


def roll(row_id: int, rng=random) -> list[tuple[int, int]]:
    out = []
    for e in str((DAILY.get(row_id) or {}).get("Reward") or "").split(","):
        parts = e.split("_")
        if len(parts) == 3 and rng.randrange(10000) < int(parts[2]):
            out.append((int(parts[0]), int(parts[1])))
    return out


def open_box(item: int, rng=random) -> list[int] | None:
    """A coach box (Useway 4, gift Randomreward) -> the coach items it gives; None if not such a box."""
    row = ITEMS.get(item) or {}
    gift = GIFTS.get(row.get("Itemresult")) if row.get("Useway") == 4 else None
    entries = [e.split(":") for e in (gift or {}).get("Randomreward") or [] if isinstance(e, str)]
    if not entries:
        return None
    w, i, a = rng.choices(entries, [int(e[0]) for e in entries])[0]
    return [int(i)] * int(a)


async def give(client, rolled: list[tuple[int, int]]):
    """Credit rewards of the trials: coach boxes open into coaches (dismantled or kept by the player's
    setting), the rest is merged; sends the changes, the pop-up and the coach achievements."""
    P = _P()
    import coach
    gid, acc = int(client.gid), client.account
    setting = coach_setting(gid, acc)
    plain, shown, decomp, new_coaches = [], [], [], []
    if True:
        for item, n in rolled:
            if open_box(item, random.Random(0)) is None:
                plain.append((item, n))
                continue
            coaches = [ci for _ in range(n) for ci in open_box(item)]  # one coach per box
            for ci in coaches:
                conf = (ITEMS.get(ci) or {}).get("Itemresult")
                rare = coach.COACHES.get(conf, {}).get("CoachRare", 0)
                if setting.auto_resolve_rare and rare <= setting.auto_resolve_rare:
                    plain.append((COACH_RESOLVE, coach.LEVELS.get((rare, 1), {}).get("Resolve") or 0))
                    decomp.append(ci)
                else:
                    new_coaches.append((ci, conf, rare))
    merged: dict[int, int] = {}
    for item, n in plain:
        merged[item] = merged.get(item, 0) + n
    # captured: the item change (0x0802) before the currencies (0x0405)
    await pvp.HOOKS["grant_rewards"](client, sorted(merged.items(), key=lambda e: acc.is_currency(e[0])), False)
    have = coach.cards(gid, acc)
    next_inst = max(list(have) + [1559926981427148799]) + 1
    for ci, conf, rare in new_coaches:
        card = P.CoachCard(inst_id=next_inst, conf_id=conf, level=1, create_time=int(time.time()))
        next_inst += 1
        coach._save(gid, card)
        await client.send_packet(coach.MSG_UPDATE, P.CoachUpdateS2C(coach_cards=[card]).SerializeToString())
    note = P.NotifyRewardsS2C()  # captured order: 336 (with the dismantled coaches), kept coaches, the rest
    if COACH_RESOLVE in merged:
        note.items.add(conf_id=COACH_RESOLVE, number=merged[COACH_RESOLVE])
    for ci, _, _ in new_coaches:
        note.items.add(conf_id=ci, number=1)
    for item, n in merged.items():
        if item != COACH_RESOLVE:
            note.items.add(conf_id=item, number=n)
    for ci in decomp:
        note.items_decomp.add(conf_id=ci, number=1)
    await client.send_packet(0x0404, note.SerializeToString())
    for _, _, rare in new_coaches:
        coach_obtained(client, rare)
    await coach._flush_missions(client)


async def handle_buy(client, pb: bytes):
    P = _P()
    import coach
    gid, acc = int(client.gid), client.account
    num = P.HighSchoolTrialsDailyRewardsBuyC2S.FromString(pb).num or 1
    st = _state(gid, acc)
    prices = [PRICES[min(st["buys"] + k, len(PRICES) - 1)] for k in range(num)] if PRICES else []
    cost: dict[int, int] = {}
    for cid, n in prices:
        cost[cid] = cost.get(cid, 0) + n
    if not prices or st["buys"] + num > MAX_BUYS or any(acc.get_currency(c) < n for c, n in cost.items()):
        await client.send_packet(MSG_BUY, P.HighSchoolTrialsDailyRewardsBuyS2C(err=1).SerializeToString())
        return
    for cid, n in cost.items():
        await client.send_packet(0x0405, wire.build([(1, 2, acc.set_currency(cid, acc.get_currency(cid) - n))]))
    acc.save()
    import missions
    for cid, n in cost.items():
        await pvp.HOOKS["mission_event"](client, missions.SPEND_CURRENCY, n, (cid,))
    st["buys"] += num
    pvp.save()
    await client.send_packet(MSG_INFO, P.HighSchoolTrialsInfoS2C(base=base(gid, acc)).SerializeToString())

    rolled = [e for _ in range(num) for e in roll(_base(acc).daily_reward_id)]
    await give(client, rolled)
    await client.send_packet(MSG_BUY, b"")


async def handle_daily(client, pb: bytes):
    """0x5602: the free daily reward, once per game day."""
    P = _P()
    gid, acc = int(client.gid), client.account
    _state(gid, acc)
    b = base(gid, acc)
    if b.daily_reward_state or b.daily_reward_id not in DAILY:
        await client.send_packet(MSG_DAILY, P.HighSchoolTrialsDailyRewardsS2C(err=1).SerializeToString())
        return
    _state(gid, acc)["daily"] = True
    pvp.save()
    await client.send_packet(MSG_INFO, P.HighSchoolTrialsInfoS2C(base=base(gid, acc)).SerializeToString())
    await give(client, roll(b.daily_reward_id))
    await client.send_packet(MSG_DAILY, b"")


async def handle_total(client, pb: bytes):
    """0x5604: every stage reward reached and not taken yet (the request has no fields)."""
    P = _P()
    gid, acc = int(client.gid), client.account
    _state(gid, acc)
    b, total = base(gid, acc), total_stages(acc)
    rows = sorted((r for r in STAGE_REWARDS.values() if b.cumulative_reward_id < (r.get("AwardConditions") or 0) <= total),
                  key=lambda r: r["AwardConditions"])
    if not rows:
        await client.send_packet(MSG_TOTAL, P.HighSchoolTrialsTotalRewardsS2C(err=1).SerializeToString())
        return
    _state(gid, acc)["cum"] = rows[-1]["AwardConditions"]
    pvp.save()
    await client.send_packet(MSG_INFO, P.HighSchoolTrialsInfoS2C(base=base(gid, acc)).SerializeToString())
    await give(client, [e for r in rows for e in stage_rewards(r)])
    await client.send_packet(MSG_TOTAL, b"")


def coach_setting(gid: int, acc):
    """The player's CoachCardSetting: their own from 0x5506 (coach.py), else the baseline's."""
    P = _P()
    import coach
    try:
        mine = coach._store(gid).get("setting")
        if mine is not None:
            return P.CoachCardSetting.FromString(bytes.fromhex(mine))
        return P.CoachInfoS2C.FromString(acc.b.pb("0x5501")).setting
    except Exception:
        return P.CoachCardSetting()


def coach_obtained(client, rare: int):
    """Achievement 91201 [rarity]: +1 per coach of that rarity obtained (8401504 [4]: 964 -> 966 for two)."""
    import gameconfig
    from accounts import TASK_FINISH
    m = getattr(client, "missions", None)
    if m is None:
        return
    for tid in m.acc.all_task_ids():
        cfg = gameconfig.TASKS.get(tid, {})
        if cfg.get("TaskSubTargetType") != COACH_GET_ACH or cfg.get("TastConditionTow") != rare:
            continue
        if m.acc.task_state(tid) != 0:
            continue
        progress = wire.get(wire.parse(m.acc.task(tid)), 3, 0) + 1
        target = cfg.get("Value") or 1
        m._set(tid, TASK_FINISH if progress >= target else 0, min(progress, target))


HANDLERS = {MSG_DAILY: handle_daily, MSG_BUY: handle_buy, MSG_TOTAL: handle_total}
