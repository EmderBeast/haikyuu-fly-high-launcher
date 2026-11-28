"""
Home (dorm) schedules: the players a manager plans into the three schedules earn something every 15
minutes while they have energy. 0x2314 HomeScheduleRewardRefreshC2S{schedule_level_id} asks the server
to bring the schedules up to date.

Shown by the Garena captures (Haikyu-Fly-High-Analyse/capture, 30 refreshes in 16 recordings; only the
server's side was recorded, the request field is from the proto):
  - the reply is a HomeUpdateS2C (0x2302) with every planned player and every schedule, then
    HomeScheduleRewardRefreshS2C{schedule_level_id}
  - every 900 s (homesteadschedulelevel DropTime[0]) a schedule earns once and each of its players loses
    6 energy (DropTime[1]); schedule_time is the time of the last earning, schedule_next_reward_time the
    next one, schedule_first_reward_time the first one; rewards[0].number = earnings so far
  - one earning = Drop<n>[amount] of the level row, n = number of planned players, times
    (1 + sum of homesteadqualityoutput.RaiseNum of the players' rarity + homesteadschooloutput.RaiseNum
    of the largest group from one school) / 10000, rounded down: exact for all 18 captured groups
    (e.g. 100104 with three SP and one SSR from one school: 50 * 1.35 = 67)
  - a player's physical_time is the time his energy runs out (last earning + energy / 6 * 900)
  - with the last energy spent the schedule stops: the update has no schedule_next_reward_time, the
    players have energy 0 and physical_time = that last earning

Assumptions (no capture):
  - a schedule stops as soon as one of its players cannot pay the 6 energy
  - earnings stop at MaxSaveNum of the level row
  - a first refresh after a long absence catches up on every earning that was due
  - the state starts from the account's captured HomeInfoS2C, moved in time so that its last earning
    is the last 15-minute step before the first login on this server

Collecting everything (0x2317) and the daily comfort reward (0x2315) are further down.
Collecting one schedule (0x2308) is further down too. Not served here (docs/garena-abgleich.md): planning
players (0x2318) or one player's reward (0x2316).
"""
import logging
import time

import gameconfig
import matches
import pvp

P = matches.P
log = logging.getLogger("hfh")

MSG_INFO, MSG_UPDATE, MSG_REFRESH = 0x2301, 0x2302, 0x2314
LEVELS = {r["ID"]: r for r in pvp._cfg("homesteadschedulelevel")}
QUALITY = {r["ID"]: r.get("RaiseNum") for r in pvp._cfg("homesteadqualityoutput")}
SCHOOL = {r["ID"]: r.get("RaiseNum") for r in pvp._cfg("homesteadschooloutput")}


def _pos(v) -> int:
    return v if isinstance(v, int) and v > 0 else 0


def step_of(level_id: int) -> tuple[int, int]:
    """(seconds between two earnings, energy each player pays)."""
    t = LEVELS.get(level_id, {}).get("DropTime") or []
    return (t[1], t[3]) if len(t) >= 4 else (900, 6)


def earning(level_id: int, players: list[int]) -> tuple[int, int]:
    """(item, amount) of one earning of that schedule with those players."""
    row = LEVELS.get(level_id, {})
    drop = row.get(f"Drop{len(players)}") or []
    if len(drop) < 6:
        return 0, 0
    cfg = [gameconfig.PLAYERS.get(p, {}) for p in players]
    schools: dict[int, int] = {}
    for c in cfg:
        schools[c.get("SchoolID")] = schools.get(c.get("SchoolID"), 0) + 1
    raise_ = sum(_pos(QUALITY.get(c.get("Rare"))) for c in cfg) + _pos(SCHOOL.get(max(schools.values(), default=0)))
    return drop[3], drop[5] * (10000 + raise_) // 10000


def entry(client, now: float | None = None) -> dict:
    """This player's schedules: {"schedules": {level: {...}}, "players": {conf: {...}}}."""
    home = pvp.state().setdefault("home", {})
    d = home.get(str(client.gid))
    if d is None:
        info = P.HomeInfoS2C()
        try:
            info.ParseFromString(client.account.b.pb("0x2301"))
        except Exception:
            pass
        now = int(time.time() if now is None else now)
        d = {"schedules": {}, "players": {}}
        planned = {p for s in info.schedules for p in s.player_conf_ids}
        for s in info.schedules:
            step, _ = step_of(s.schedule_level_id)
            last = s.schedule_time or now
            shift = (now - last) // step * step if s.schedule_next_reward_time else 0
            d["schedules"][str(s.schedule_level_id)] = {
                "players": list(s.player_conf_ids),
                "first": s.schedule_first_reward_time + shift if s.schedule_first_reward_time else 0,
                "last": last + shift,
                "item": s.rewards[0].conf_id if s.rewards else 0,
                "amount": s.rewards[0].number if s.rewards else 0,
                "running": bool(s.schedule_next_reward_time),
            }
        for p in info.players:
            if p.conf_id in planned:
                d["players"][str(p.conf_id)] = {"physical": p.physical, "reward": p.reward}
        home[str(client.gid)] = d
    return d


def advance(d: dict, now: float | None = None) -> int:
    """Lets every earning that is due happen; returns how many there were."""
    now = int(time.time() if now is None else now)
    done = 0
    for level, s in d["schedules"].items():
        step, cost = step_of(int(level))
        item, amount = earning(int(level), s["players"])
        cap = _pos(LEVELS.get(int(level), {}).get("MaxSaveNum"))
        while s["running"] and s["players"] and s["last"] + step <= now:
            energy = [d["players"].setdefault(str(p), {"physical": 0, "reward": False}) for p in s["players"]]
            if any(e["physical"] < cost for e in energy):
                s["running"] = False
                break
            for e in energy:
                e["physical"] -= cost
            s["last"] += step
            s["first"] = s["first"] or s["last"]
            s["item"] = s["item"] or item
            s["amount"] = min(s["amount"] + amount, cap) if cap else s["amount"] + amount
            done += 1
            if any(e["physical"] < cost for e in energy):
                s["running"] = False
    return done


def update(d: dict) -> "P.HomeUpdateS2C":
    """HomeUpdateS2C with every planned player and every schedule, as Garena sends it on a refresh."""
    out = P.HomeUpdateS2C()
    ends = {}
    for level, s in d["schedules"].items():
        step, cost = step_of(int(level))
        for p in s["players"]:
            left = d["players"].get(str(p), {}).get("physical", 0)
            ends[p] = s["last"] + (left // cost) * step
            e = d["players"].get(str(p), {})
            out.players.add(conf_id=p, physical=left, physical_time=ends[p], reward=bool(e.get("reward")))
        m = out.schedules.add(schedule_level_id=int(level), schedule_time=s["last"], player_conf_ids=s["players"],
                              schedule_first_reward_time=s["first"])
        if s["amount"]:
            m.rewards.add(conf_id=s["item"], number=s["amount"])
        if s["running"]:
            m.schedule_next_reward_time = s["last"] + step
    return out


async def handle_refresh(client, pb: bytes):
    req = P.HomeScheduleRewardRefreshC2S.FromString(pb)
    d = entry(client)
    n = advance(d)
    if n:
        pvp.save()
    await client.send_packet(MSG_UPDATE, update(d).SerializeToString())
    await client.send_packet(MSG_REFRESH, P.HomeScheduleRewardRefreshS2C(
        schedule_level_id=req.schedule_level_id).SerializeToString())
    log.info(f"  HOME refresh {req.schedule_level_id}: {n} earning(s)")


# --- planning players into a schedule -------------------------------------------------------------------
# 0x2307 HomeSchedulePlanC2S{schedule_level_id, players, friend_ids} -> HomeSchedulePlanS2C{err}. Had no
# handler (empty reply): the players did not stay in the schedule. Shown by the capture of "plan all"
# (0x2318, docs/garena-abgleich.md): planned players have 300 energy and the schedule's first earning comes
# 900 s later; the state goes out as HomeUpdateS2C. Assumptions (no capture of 0x2307): the same for one
# schedule - the named players replace the schedule's players, each gets 300 energy, the schedule starts
# now (what it had earned stays); friends (friend_ids) are not served; an empty list stops the schedule.
MSG_PLAN = 0x2307
PLAN_ENERGY = 300


async def handle_plan(client, pb: bytes):
    req = P.HomeSchedulePlanC2S.FromString(pb)
    d = entry(client)
    advance(d)
    now = int(time.time())
    players = list(dict.fromkeys(req.players))
    s = d["schedules"].setdefault(str(req.schedule_level_id), {"players": [], "first": 0, "last": now,
                                                                "item": 0, "amount": 0, "running": False})
    for other_level, other in d["schedules"].items():  # a player works in one schedule at a time
        if other_level != str(req.schedule_level_id):
            other["players"] = [p for p in other["players"] if p not in players]
    s["players"], s["last"], s["running"] = players, now, bool(players)
    for p in players:
        d["players"][str(p)] = {"physical": PLAN_ENERGY, "reward": d["players"].get(str(p), {}).get("reward", False)}
    pvp.save()
    await client.send_packet(MSG_UPDATE, update(d).SerializeToString())
    await client.send_packet(MSG_PLAN, P.HomeSchedulePlanS2C().SerializeToString())
    log.info(f"  HOME plan {req.schedule_level_id}: players {players}")


# --- collecting what the schedules earned --------------------------------------------------------------
# 0x2317 HomeRewardC2S (no fields): take everything. Garena capture (decrypted_1, messages 0068-0071):
#   - HomeUpdateS2C with the schedules only, now without rewards and without first / next reward time
#     (all three had stopped), then the currency change, NotifyRewardsS2C with the sum per currency and
#     the empty HomeRewardS2C
#   - the sums are exactly 50 earnings of each schedule (300 energy / 6): 102 x56000 = 50 x 1120,
#     100 x168750 = 50 x 3375, 227 x3350 = 50 x 67
# Assumptions: a schedule that is still running keeps running (its next reward time stays); with
# nothing to take only the reply is sent.
MSG_COLLECT = 0x2317


async def handle_collect(client, pb: bytes):
    d = entry(client)
    advance(d)
    rewards: dict[int, int] = {}
    for s in d["schedules"].values():
        if s["amount"]:
            rewards[s["item"]] = rewards.get(s["item"], 0) + s["amount"]
            s["amount"], s["first"] = 0, 0
    pvp.save()
    if rewards:
        up = update(d)
        del up.players[:]
        await client.send_packet(MSG_UPDATE, up.SerializeToString())
        await pvp.HOOKS["grant_rewards"](client, list(rewards.items()), False)
        popup = P.NotifyRewardsS2C()
        for item, n in rewards.items():
            popup.items.add(conf_id=item, number=n)
        await client.send_packet(0x0404, popup.SerializeToString())
    await client.send_packet(MSG_COLLECT, b"")
    log.info(f"  HOME collect: {rewards}")


# 0x2308 HomeScheduleRewardC2S{schedule_level_id}: take what one schedule earned. Garena capture garena4
# (26.11., schedule 100404, still running): HomeUpdateS2C with only that schedule - schedule_time and the
# players unchanged, no rewards, schedule_first_reward_time = schedule_next_reward_time = last earning + 900 s -,
# then the currency change, NotifyRewardsS2C {100 x23625} (the stock the room showed) and
# HomeScheduleRewardS2C{schedule_level_id}.
# Assumptions: a stopped schedule gets no first / next reward time (as with 0x2317); an unknown schedule
# or one with nothing earned is answered FAILED.
MSG_COLLECT_ONE = 0x2308


async def handle_collect_one(client, pb: bytes):
    level = P.HomeScheduleRewardC2S.FromString(pb).schedule_level_id
    d = entry(client)
    advance(d)
    s = d["schedules"].get(str(level))
    if not s or not s["amount"]:
        log.info(f"  HOME collect {level}: nothing")
        await client.send_packet(MSG_COLLECT_ONE, P.HomeScheduleRewardS2C(
            err=FAILED, schedule_level_id=level).SerializeToString())
        return
    item, amount = s["item"], s["amount"]
    s["amount"] = 0
    step, _ = step_of(level)
    s["first"] = s["last"] + step if s["running"] else 0
    pvp.save()
    up = update(d)
    del up.players[:]
    keep = [x for x in up.schedules if x.schedule_level_id == level]
    del up.schedules[:]
    up.schedules.extend(keep)
    await client.send_packet(MSG_UPDATE, up.SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, [(item, amount)], False)
    popup = P.NotifyRewardsS2C()
    popup.items.add(conf_id=item, number=amount)
    await client.send_packet(0x0404, popup.SerializeToString())
    await client.send_packet(MSG_COLLECT_ONE, P.HomeScheduleRewardS2C(schedule_level_id=level).SerializeToString())
    log.info(f"  HOME collect {level}: {item} x{amount}")


# --- comfort: the daily reward ----------------------------------------------------------------------
# 0x2315 HomeComfortRewardC2S{level = -1}. Garena captures (decrypted_3 / decrypted_2_reconnect, one claim):
#   - the account had comfort 5085 = level 20 of homesteadcomfort (ComfortNumber 5000); it got that row's
#     DailyGiftPack: 227 x3900, 117 x48 and the box 102000011, which opened at once (popup: 102 x2500, one
#     of the entries of its gift 1074)
#   - order: currency change, NotifyRewardsS2C, TaskChangeS2C (weekly mission 6000015 "earn 117": +48),
#     then HomeComfortRewardS2C{level = -1, comfort_daily_reward_unix = the next 06:00}
# Assumptions: asked again before that time, the reply only repeats the time; the comfort value is the
# account's baseline value (nothing on this server changes it yet).
# Level rewards (level >= 1, "Level Pack" of the comfort info): no capture of such a claim. From the client
# (HomesteadMessageHandler::OnReceiveClaimComfortReward / HomesteadBaseData::HasComfortLevelRewardClaimed):
# the reply carries the new comfort_level_rewarded, a bit mask with bit <level> set per claimed level.
# Assumed: a reached level that is not claimed yet gives its LevelGiftPack once (boxes that open by themselves
# opened), in the same order as the daily reward. Both the next daily time and the mask are kept per account
# and put into the login HomeInfoS2C (patch_info); before, a relog showed the daily pack as open again.
MSG_COMFORT = 0x2315
DAILY_REWARD, HOME_COIN, EARN_CURRENCY = -1, 117, 80104
COMFORT = sorted(pvp._cfg("homesteadcomfort"), key=lambda r: r.get("ComfortNumber") or 0)


def comfort_row(total: int) -> dict | None:
    """The highest comfort level reached with that comfort value."""
    reached = [r for r in COMFORT if (r.get("ComfortNumber") or 0) <= total]
    return reached[-1] if reached else None


def daily_pack(row: dict, rng=None, field: str = "DailyGiftPack") -> list[tuple[int, int]]:
    """DailyGiftPack (or LevelGiftPack) of the level, boxes that open by themselves replaced by what they hold."""
    import monthcard
    import pack
    import random
    won: dict[int, int] = {}
    for item, n in monthcard.pairs(row.get(field)):
        gift = pack.GIFTS.get(pack.ITEMS.get(item, {}).get("Itemresult"))             if isinstance(pack.ITEMS.get(item, {}).get("Itemresult"), int) else None
        if gift and gift.get("Autoopen") == 1 and pack.ITEMS[item].get("Droptype") == pack.DROP_GIFT:
            for i, k in pack.open_boxes(gift, n, rng or random):
                won[i] = won.get(i, 0) + k
        else:
            won[item] = won.get(item, 0) + n
    return list(won.items())


async def handle_comfort_reward(client, pb: bytes):
    import freepack
    req = P.HomeComfortRewardC2S.FromString(pb)
    info = _base_info(client)  # with this account's base level and comfort
    d = entry(client)
    if req.level != DAILY_REWARD:
        await _comfort_level_reward(client, req.level, info, d)
        return
    now = int(time.time())
    due = d.get("comfort_next", info.base_info.comfort_daily_reward_unix)
    row = comfort_row(info.base_info.comfort.total)
    if now >= due and row:
        rewards = daily_pack(row)
        d["comfort_next"] = due = freepack.day_start(now) + 86400
        pvp.save()
        await pvp.HOOKS["grant_rewards"](client, rewards, False)
        popup = P.NotifyRewardsS2C()
        for item, n in rewards:
            popup.items.add(conf_id=item, number=n)
        await client.send_packet(0x0404, popup.SerializeToString())
        coins = sum(n for item, n in rewards if item == HOME_COIN)
        if coins:
            await pvp.HOOKS["mission_event"](client, EARN_CURRENCY, coins, (HOME_COIN,))
        log.info(f"  HOME comfort level {row['ID']}: daily reward {rewards}")
    await client.send_packet(MSG_COMFORT, P.HomeComfortRewardS2C(
        level=DAILY_REWARD, comfort_daily_reward_unix=due).SerializeToString())


async def _comfort_level_reward(client, level: int, info, d: dict):
    mask = int(d.get("comfort_rewarded", info.base_info.comfort_level_rewarded))
    row = next((r for r in COMFORT if r.get("ID") == level), None)
    now_row = comfort_row(info.base_info.comfort.total)
    if row is None or now_row is None or level > now_row["ID"] or level < 0 or level > 63 or mask >> level & 1:
        log.info(f"  HOME comfort level reward {level}: refused (mask {mask:b})")
        await client.send_packet(MSG_COMFORT, P.HomeComfortRewardS2C(
            err=FAILED, level=level, comfort_level_rewarded=mask).SerializeToString())
        return
    rewards = daily_pack(row, field="LevelGiftPack")
    d["comfort_rewarded"] = mask = mask | 1 << level
    pvp.save()
    await pvp.HOOKS["grant_rewards"](client, rewards, False)
    popup = P.NotifyRewardsS2C()
    for item, n in rewards:
        popup.items.add(conf_id=item, number=n)
    await client.send_packet(0x0404, popup.SerializeToString())
    coins = sum(n for item, n in rewards if item == HOME_COIN)
    if coins:
        await pvp.HOOKS["mission_event"](client, EARN_CURRENCY, coins, (HOME_COIN,))
    log.info(f"  HOME comfort level {level}: level reward {rewards}")
    await client.send_packet(MSG_COMFORT, P.HomeComfortRewardS2C(
        level=level, comfort_level_rewarded=mask).SerializeToString())


# --- garden: sowing -----------------------------------------------------------------------------------
# 0x231A HomeGardenLandWeedC2S{1=status 2=inst_id (list) 3=weed_id}; status 0 = sow (HomesteadSowViewLogic::
# OnEndDrag with the chosen seed), 1 = uproot, 3 = harvest all (weed_id 0). Reply HomeGardenLandWeedS2C{1=err
# 2=status 3=repeated HomeFurniture}. Garena captures:
#   - decrypted_3 0127/0128: 12 lands sown with 4002005, no cost message; first the HomeUpdateS2C with the
#     watering machines (start_time = sowing, update_time = ripe) and the lands, then the reply with the lands
#   - GardenLand{1=weed 2=decrease 3=start 6=water_inst_id 7=water_start 8=update_time 9=gain_times}: the land
#     can be harvested at gain_times; for 4002005 (RipeningTime [2, 5, 7] h, GainNum [7, 7]) they are +16 800 and
#     +33 600 s = 1 and 2 times 7 h / 1.5 with a machine of Funtion 5000 (+50 %); update_time = the last one.
#     The account of the baseline (sown 21.11.) shows the same times.
#   - which machine waters a land: GardenSizeMath::CalculateCoverRect (client): the machine's CoverAreaSize
#     (12 x 12) from its anchor, shifted by effect_area (0 centred, 1 +x +z, 2 +x -z, 3 -x +z, 4 -x -z by
#     cover - machine size). With a machine footprint of 4 x 4 and "the land touches the area" this gives the
#     machine of all 24 lands of both accounts.
# Assumptions: the footprint 4 x 4 (not in the tables); a land with two machines takes the first; a land
# without a machine grows at normal speed; one harvest per GainNum entry, each RipeningTime[-1] later; only
# gardenfurniture Type 1 machines speed up growth; sowing a land that is not empty is refused.
MSG_LAND_WEED = 0x231A
SOW, UPROOT, HARVEST_ONE, HARVEST = 0, 1, 2, 3
LAND_CONF = 4001001
WEEDS = {r["ID"]: r for r in pvp._cfg("gardenweed")}
MACHINES = {r["ID"]: r for r in pvp._cfg("gardenfurniture")}
MACHINE_SIZE = 4
FAILED = 1


def _garden(d: dict) -> dict:
    return d.setdefault("garden", {"lands": {}, "machines": {}})


def furnitures(client) -> dict:
    """Every garden land and machine of this home: the baseline's, with this player's changes."""
    info = P.HomeInfoS2C()
    try:
        info.ParseFromString(client.account.b.pb("0x2301"))
    except Exception:
        pass
    g = _garden(entry(client))
    out = {}
    for sc in info.furnitures_scene:
        for f in sc.furnitures:
            if f.conf_id == LAND_CONF or f.conf_id in MACHINES:
                c = P.HomeFurniture()
                c.CopyFrom(f)
                if str(f.inst_id) in g["lands"]:
                    c.land.Clear()
                    c.land.ParseFromString(bytes.fromhex(g["lands"][str(f.inst_id)]))
                if str(f.inst_id) in g["machines"]:
                    c.machine.Clear()
                    c.machine.ParseFromString(bytes.fromhex(g["machines"][str(f.inst_id)]))
                out[f.inst_id] = c
    return out


def cover_rect(m) -> tuple:
    size = MACHINES.get(m.conf_id, {}).get("CoverAreaSize") or [12, 12]
    cw, ch = size[0], size[-1]
    dx, dz = {0: ((cw - MACHINE_SIZE) >> 1, (ch - MACHINE_SIZE) >> 1), 1: (0, 0), 2: (0, ch - MACHINE_SIZE),
              3: (cw - MACHINE_SIZE, 0), 4: (cw - MACHINE_SIZE, ch - MACHINE_SIZE)}.get(m.machine.effect_area, (0, 0))
    return m.coord.x - dx, m.coord.z - dz, cw, ch


def watering(land, machines):
    """The first speed machine (gardenfurniture Type 1) whose area the land touches."""
    for m in machines:
        if MACHINES.get(m.conf_id, {}).get("Type") != 1 or condition(m, int(time.time())) <= 0:
            continue
        x, z, w, h = cover_rect(m)
        if (land.coord.x < x + w and land.coord.x + MACHINE_SIZE > x
                and land.coord.z < z + h and land.coord.z + MACHINE_SIZE > z):
            return m
    return None


CEMENT = 227


def machine_work(m, now: int) -> int:
    """Seconds the machine has watered: `work` up to start_time, plus the time it has run since (until
    update_time). Capture wcy2611: work 19161 + 51 s since start = 19212 = 22.24 % of Repaired 86 400 ->
    the repair record's before 777 638 (millionths) and the 78 % the game showed."""
    run = max(0, min(now, m.machine.update_time) - m.machine.start_time) if m.machine.start_time else 0
    return m.machine.work + run


def condition(m, now: int) -> int:
    """Machine condition in millionths: 1 - worked / gardenfurniture.Repaired."""
    full = MACHINES.get(m.conf_id, {}).get("Repaired") or 86400
    return max(0, (full - machine_work(m, now)) * 1_000_000 // full)


def run_machine(m, now: int, until: int):
    """A machine starts watering (or waters longer): an idle machine folds its last run into `work` and
    starts now; a running one keeps its start (wcy2611: the second harvest 21 s later only moved update_time)."""
    if m.machine.start_time and now < m.machine.update_time:
        m.machine.update_time = max(m.machine.update_time, until)
        return
    m.machine.work = machine_work(m, now)
    m.machine.start_time, m.machine.update_time = now, until


def gain_times(weed: dict, start: int, machine, rounds: int | None = None) -> list:
    ripe = (weed.get("RipeningTime") or [0])[-1] * 3600
    if machine is not None:
        ripe = ripe * 10000 // (10000 + (MACHINES.get(machine.conf_id, {}).get("Funtion") or 0))
    if rounds is None:
        g = weed.get("GainNum")
        rounds = len(g) if isinstance(g, list) else 1
    return [start + ripe * (i + 1) for i in range(rounds)]


async def handle_land_weed(client, pb: bytes, now: float | None = None):
    req = P.HomeGardenLandWeedC2S.FromString(pb)
    now = int(time.time() if now is None else now)
    if req.status in (HARVEST, HARVEST_ONE):
        await harvest(client, req, now)
        return
    if req.status == UPROOT:
        await uproot(client, req)
        return
    if req.status != SOW:
        await pvp.HOOKS["generic"](client, pb, MSG_LAND_WEED)
        return
    homes = furnitures(client)
    lands = [homes.get(i) for i in req.inst_id]
    weed = WEEDS.get(req.weed_id)
    if not weed or not lands or any(f is None or f.conf_id != LAND_CONF or f.land.weed_id for f in lands):
        log.info(f"  GARDEN sow {req.weed_id} on {list(req.inst_id)}: refused")
        await client.send_packet(MSG_LAND_WEED, P.HomeGardenLandWeedS2C(err=FAILED, status=SOW).SerializeToString())
        return
    g = _garden(entry(client))
    machines = [f for f in homes.values() if f.conf_id in MACHINES]
    used = {}
    for f in lands:
        m = watering(f, machines)
        times = gain_times(weed, now, m)
        decrease = f.land.decrease
        f.land.Clear()
        f.land.weed_id, f.land.decrease, f.land.start_time = req.weed_id, decrease, now
        if m is not None:
            f.land.water_inst_id, f.land.water_start_time = m.inst_id, now
            used[m.inst_id] = max(used.get(m.inst_id, 0), times[-1])
        f.land.update_time = times[-1]
        f.land.gain_times.extend(times)
        g["lands"][str(f.inst_id)] = f.land.SerializeToString().hex()
    upd = P.HomeUpdateS2C()
    for inst, until in used.items():
        m = homes[inst]
        run_machine(m, now, until)
        g["machines"][str(inst)] = m.machine.SerializeToString().hex()
        upd.furnitures.add().CopyFrom(m)
    for f in lands:
        upd.furnitures.add().CopyFrom(f)
    pvp.save()
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    out = P.HomeGardenLandWeedS2C(status=SOW)
    for f in lands:
        out.furniture.add().CopyFrom(f)
    await client.send_packet(MSG_LAND_WEED, out.SerializeToString())
    log.info(f"  GARDEN sow {req.weed_id} on {len(lands)} land(s), ripe at {lands[0].land.update_time}")


# --- garden: harvesting -------------------------------------------------------------------------------
# 0x231A with status 3 = every ripe land (HomesteadMainViewLogic::OnAllHarvestBtnEvent, weed_id 0) or
# status 2 = one land (tapping it). Garena captures:
#   - decrypted_1 0076-0079, status 3: 12 lands of 4002004 (GainNum 8, one round) -> 920000104 x96; the
#     lands are emptied (only `decrease` stays). Order: FurniturePackItemChangeS2C (gain_items with the new
#     total), NotifyRewardsS2C, HomeUpdateS2C with the lands, the reply {status, lands}
#   - wcy2611 (own recording, 26.11.), status 2 twice: lands of 4002002 (GainNum [16, 16, 16], GainLimit 32,
#     CostValue 1000) ripe to the limit ("MAX", no gain_times left) -> 32 each = two rounds; the third round
#     grows again from the harvest: start_time = now, gain_round 2, gain_times [now + 4 h / 1.5] with its
#     sprinkler, decrease 7000 -> 10000, and 1000 cement (227) are taken = CostValue. Order: currency, pack
#     change, popup, HomeUpdate (land and machine), reply.
# Crops live in the furniture pack (FurniturePackItemInfoS2C.gain_items at login, the baseline's).
# Assumptions: the rounds whose time has come are taken in order as long as their sum stays within
# GainLimit; rounds left over grow again from the harvest, each one ripening time after the other; a land
# with nothing ripe is left out (nothing at all -> FAILED); not enough cement for the regrowth -> FAILED.
MSG_PACK_CHANGE = 0x2202
FULL_SOIL = 10000


def _crops(client) -> dict:
    """conf -> Item of the crops: the baseline's gain_items with this player's totals."""
    pack = P.FurniturePackItemInfoS2C()
    try:
        pack.ParseFromString(client.account.b.pb("0x2201"))
    except Exception:
        pass
    out = {i.conf_id: i for i in pack.gain_items}
    for conf, e in _garden(entry(client)).setdefault("crops", {}).items():
        item = out.setdefault(int(conf), P.Item(inst_id=e["inst"], conf_id=int(conf), create_time=e["time"]))
        item.number = e["number"]
    return out


def add_crop(client, conf: int, number: int, now: int) -> "P.Item":
    have = _crops(client).get(conf)
    store = _garden(entry(client)).setdefault("crops", {})
    if have is None:
        e = store[str(conf)] = {"inst": client.account._new_inst(), "number": 0, "time": now}
        client.account.save()
    else:
        e = store.setdefault(str(conf), {"inst": have.inst_id, "number": have.number, "time": have.create_time})
    e["number"] += number
    return P.Item(inst_id=e["inst"], conf_id=conf, number=e["number"], create_time=e["time"])


def _gain_num(weed: dict, i: int) -> int:
    g = weed.get("GainNum")
    if isinstance(g, list):
        return g[i] if i < len(g) else 0
    return g or 0


def _rounds(weed: dict) -> int:
    g = weed.get("GainNum")
    return len(g) if isinstance(g, list) else 1


def take(land, weed: dict, now: int) -> tuple[int, int]:
    """(crops, rounds) a harvest takes from the land now."""
    left = _rounds(weed) - land.gain_round
    ripe = left if not land.gain_times else sum(1 for t in land.gain_times if t <= now)
    limit = weed.get("GainLimit") or 0
    got = rounds = 0
    for i in range(land.gain_round, land.gain_round + min(ripe, left)):
        n = _gain_num(weed, i)
        if limit and got + n > limit:
            break
        got, rounds = got + n, rounds + 1
    return got, rounds


async def harvest(client, req, now: int):
    homes = furnitures(client)
    g = _garden(entry(client))
    machines = [f for f in homes.values() if f.conf_id in MACHINES]
    lands, won, cost, used = [], {}, 0, {}
    plan = []
    for inst in req.inst_id:
        f = homes.get(inst)
        if f is None or f.conf_id != LAND_CONF or not f.land.weed_id:
            continue
        weed = WEEDS.get(f.land.weed_id, {})
        got, rounds = take(f.land, weed, now)
        if not rounds:
            continue
        regrow = _rounds(weed) - f.land.gain_round - rounds
        cost += (weed.get("CostValue") or 0) if regrow > 0 else 0
        plan.append((f, weed, got, rounds, regrow))
    acc = client.account
    if not plan or acc.get_currency(CEMENT) < cost:
        log.info(f"  GARDEN harvest {list(req.inst_id)}: nothing ripe or not enough cement ({cost})")
        await client.send_packet(MSG_LAND_WEED, P.HomeGardenLandWeedS2C(err=FAILED, status=req.status).SerializeToString())
        return
    for f, weed, got, rounds, regrow in plan:
        crop = weed.get("GainItemID")
        won[crop] = won.get(crop, 0) + got
        if regrow <= 0:
            decrease = f.land.decrease
            f.land.Clear()
            f.land.decrease = decrease
        else:
            m = watering(f, machines)
            times = gain_times(weed, now, m, regrow)
            weed_id, done = f.land.weed_id, f.land.gain_round + rounds
            f.land.Clear()
            f.land.weed_id, f.land.decrease, f.land.start_time, f.land.gain_round = weed_id, FULL_SOIL, now, done
            if m is not None:
                f.land.water_inst_id, f.land.water_start_time = m.inst_id, now
                used[m.inst_id] = max(used.get(m.inst_id, 0), times[-1])
            f.land.update_time = times[-1]
            f.land.gain_times.extend(times)
        g["lands"][str(f.inst_id)] = f.land.SerializeToString().hex()
        lands.append(f)
    if cost:
        entry_ = acc.set_currency(CEMENT, acc.get_currency(CEMENT) - cost)
        acc.save()
        import wire
        await client.send_packet(0x0405, wire.build([(1, 2, entry_)]))
    change = P.FurniturePackItemChangeS2C()
    popup = P.NotifyRewardsS2C()
    for crop, n in won.items():
        item = add_crop(client, crop, n, now)
        change.gain_items.add().CopyFrom(item)
        popup.items.add(conf_id=crop, number=n, create_time=item.create_time)
    upd = P.HomeUpdateS2C()
    out = P.HomeGardenLandWeedS2C(status=req.status)
    for f in lands:
        upd.furnitures.add().CopyFrom(f)
        out.furniture.add().CopyFrom(f)
    for inst, until in used.items():
        m = homes[inst]
        run_machine(m, now, until)
        g["machines"][str(inst)] = m.machine.SerializeToString().hex()
        upd.furnitures.add().CopyFrom(m)
    pvp.save()
    await client.send_packet(MSG_PACK_CHANGE, change.SerializeToString())
    await client.send_packet(0x0404, popup.SerializeToString())
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    await client.send_packet(MSG_LAND_WEED, out.SerializeToString())
    log.info(f"  GARDEN harvest {len(lands)} land(s): {won}, cement {cost}")


async def uproot(client, req):
    """Status 1 (HomesteadGardDetailViewLogic::OnUprootEvent: a confirmation, no cost shown): the land is
    emptied, nothing is harvested. No capture: the messages follow sowing (HomeUpdate, then the reply);
    a land without a plant -> FAILED."""
    homes = furnitures(client)
    g = _garden(entry(client))
    lands = [homes.get(i) for i in req.inst_id]
    if not lands or any(f is None or f.conf_id != LAND_CONF or not f.land.weed_id for f in lands):
        await client.send_packet(MSG_LAND_WEED, P.HomeGardenLandWeedS2C(err=FAILED, status=UPROOT).SerializeToString())
        return
    upd, out = P.HomeUpdateS2C(), P.HomeGardenLandWeedS2C(status=UPROOT)
    for f in lands:
        decrease = f.land.decrease
        f.land.Clear()
        f.land.decrease = decrease
        g["lands"][str(f.inst_id)] = f.land.SerializeToString().hex()
        upd.furnitures.add().CopyFrom(f)
        out.furniture.add().CopyFrom(f)
    pvp.save()
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    await client.send_packet(MSG_LAND_WEED, out.SerializeToString())
    log.info(f"  GARDEN uproot {len(lands)} land(s)")


# --- garden: repairing a machine --------------------------------------------------------------------------
# 0x2322 HomeGardenFurnitureRepairC2S{1=inst_id 2=count}: count = the missing percent the client shows.
# gardenfurniture.RepairedCost [8, 864, 16, 227, 24, 6] = per 1 % (864 s of Repaired 86 400 s) 6 cement.
# Garena captures:
#   - wcy2611 (own recording): machine at 78 % (work 19161 + 51 s running), count 22 -> 132 cement (22 x 6);
#     0x0405, HomeUpdateS2C{repair_records: op_time, furniture_conf_id 4001005, conf_id 227, number 132,
#     before 777 638, after 997 638 (millionths)}, then the reply {furniture} with work 153 = 19 212 - 22 x 864
#     - the 51 s of the current run (start_time and update_time unchanged)
#   - decrypted_3 0118-0124: two repairs of 33 % each -> 198 cement, after - before = 330 000 each
# Assumptions: count above what is missing is refused; not enough cement -> FAILED; the record is not kept
# for the next login.
MSG_REPAIR = 0x2322


def _repair_cost(conf: int) -> tuple[int, int, int]:
    """(seconds per percent, item, number per percent) from RepairedCost [8, s, 16, item, 24, n]."""
    v = MACHINES.get(conf, {}).get("RepairedCost") or []
    tags = {v[i]: v[i + 1] for i in range(0, len(v) - 1, 2)}
    return tags.get(8, 864), tags.get(16, CEMENT), tags.get(24, 6)


async def handle_repair(client, pb: bytes, now: float | None = None):
    req = P.HomeGardenFurnitureRepairC2S.FromString(pb)
    now = int(time.time() if now is None else now)
    homes = furnitures(client)
    m = homes.get(req.inst_id)
    acc = client.account
    if m is None or m.conf_id not in MACHINES or req.count <= 0:
        await client.send_packet(MSG_REPAIR, P.HomeGardenFurnitureRepairS2C(err=FAILED).SerializeToString())
        return
    per, item, price = _repair_cost(m.conf_id)
    worked = machine_work(m, now)
    cost = req.count * price
    if req.count > -(-worked // per) or acc.get_currency(item) < cost:
        log.info(f"  GARDEN repair {req.inst_id} x{req.count}: refused (worked {worked}, cost {cost})")
        await client.send_packet(MSG_REPAIR, P.HomeGardenFurnitureRepairS2C(err=FAILED).SerializeToString())
        return
    before = condition(m, now)
    m.machine.work -= min(worked, req.count * per)
    after = condition(m, now)
    _garden(entry(client))["machines"][str(m.inst_id)] = m.machine.SerializeToString().hex()
    entry_ = acc.set_currency(item, acc.get_currency(item) - cost)
    acc.save()
    pvp.save()
    import wire
    await client.send_packet(0x0405, wire.build([(1, 2, entry_)]))
    upd = P.HomeUpdateS2C()
    upd.repair_records.add(op_time=now, furniture_conf_id=m.conf_id, conf_id=item, number=cost, before=before, after=after)
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    await client.send_packet(MSG_REPAIR, P.HomeGardenFurnitureRepairS2C(furniture=m).SerializeToString())
    log.info(f"  GARDEN repair {m.inst_id} +{req.count} %: {before} -> {after}, {cost} x {item}")


# --- garden: orders -------------------------------------------------------------------------------------
# 0x231C HomeGardenOrderFinishC2S{1=order_id} = the slot 0..3 of today's orders. Garena captures
# (decrypted_1, 4 deliveries; own recording wcy2611, 4 deliveries at garden level 13):
#   - FurniturePackItemChangeS2C with the new totals of the crops given, HomeUpdateS2C{garden_order: the
#     order is_finish, week_order_num + 1}, the currency change, NotifyRewardsS2C, TaskChangeS2C, the reply
#     {order_id}
#   - reward = gardenclassconfig.OrderReward[order_id] of the order's level ([item, item2, n, n2]: slot 0
#     117 x5 + 100 x28500, 1: 117 x5 + 102 x19000, 2: 117 x5 + 127 x1340, 3: 117 x18 at level 13) plus garden
#     EXP 280 = sum of number x gardenweed.Value of the crop (tomato 46 x 500 = 23 000; eggplant 46 x 250 +
#     8 x 1500 = 23 500)
#   - the orders change at 06:00 UTC (wcy2611: "refreshes in 13:48:26" at 16:11:34 UTC; garena4 likewise),
#     next_order_list becomes curr_order_list (account of lev2211)
# Assumptions: how Garena draws a new next_order_list is not known, so after a change the preview stays
# empty; the weekly counter starts again on Monday 06:00 UTC; an order without enough crops, already
# finished or unknown -> FAILED.
MSG_ORDER = 0x231C
CLASSES = {r["ID"]: r for r in pvp._cfg("gardenclassconfig")}
GARDEN_EXP = 280
ORDER_HOUR = 6  # UTC


def _quads(spec) -> list[dict]:
    """OrderReward [[8, a, 16, b, 24, n, 32, m], 8, a, ...] -> one {tag: value} per slot."""
    flat = []
    for v in spec or []:
        flat.extend(v if isinstance(v, list) else [v])
    out, row = [], {}
    for i in range(0, len(flat) - 1, 2):
        if flat[i] == 8 and row:
            out.append(row)
            row = {}
        row[flat[i]] = flat[i + 1]
    if row:
        out.append(row)
    return out


def order_reward(level: int, slot: int, weeds) -> list[tuple[int, int]]:
    rows = _quads(CLASSES.get(level, {}).get("OrderReward"))
    out = []
    if slot < len(rows):
        r = rows[slot]
        if 8 in r:
            out.append((r[8], r.get(24, 1)))
        if 16 in r:
            out.append((r[16], r.get(32, 1)))
    value = {w.get("GainItemID"): (w.get("Value") or [0, 0, 0, 0])[-1] for w in WEEDS.values()}
    exp = sum(n.number * value.get(n.conf_id, 0) for n in weeds)
    if exp:
        out.append((GARDEN_EXP, exp))
    return out


def _last_change(now: int) -> int:
    day = now - (now - ORDER_HOUR * 3600) % 86400
    return day


def orders(client, now: int) -> "P.HomeGardenOrderData":
    """Today's orders: the baseline's (or this player's), moved on at every 06:00 UTC since."""
    g = _garden(entry(client))
    data = P.HomeGardenOrderData()
    if "orders" in g:
        data.ParseFromString(bytes.fromhex(g["orders"]))
    else:
        info = P.HomeInfoS2C()
        try:
            info.ParseFromString(client.account.b.pb("0x2301"))
        except Exception:
            pass
        data.CopyFrom(info.garden_order)
    change = _last_change(now)
    if data.last_refresh_time < change:
        week = change - ((change // 86400 + 3) % 7) * 86400  # Monday (1970-01-01 was a Thursday)
        if data.last_refresh_time < week:
            data.week_order_num, data.week_reward_received = 0, False
        nxt = list(data.next_order_list)
        del data.curr_order_list[:]
        del data.next_order_list[:]
        for o in nxt:
            c = data.curr_order_list.add()
            c.CopyFrom(o)
            c.is_finish = False
        data.last_refresh_time = change
        g["orders"] = data.SerializeToString().hex()
    return data


async def handle_order(client, pb: bytes, now: float | None = None):
    req = P.HomeGardenOrderFinishC2S.FromString(pb)
    now = int(time.time() if now is None else now)
    data = orders(client, now)
    crops = _crops(client)
    order = data.curr_order_list[req.order_id] if 0 <= req.order_id < len(data.curr_order_list) else None
    if order is None or order.is_finish or any(
            crops.get(w.conf_id) is None or crops[w.conf_id].number < w.number for w in order.weed_list):
        log.info(f"  GARDEN order {req.order_id}: refused")
        await client.send_packet(MSG_ORDER, P.HomeGardenOrderFinishS2C(err=FAILED, order_id=req.order_id).SerializeToString())
        return
    change = P.FurniturePackItemChangeS2C()
    for w in order.weed_list:
        change.gain_items.add().CopyFrom(add_crop(client, w.conf_id, -w.number, now))
    order.is_finish = True
    data.week_order_num += 1
    _garden(entry(client))["orders"] = data.SerializeToString().hex()
    pvp.save()
    rewards = order_reward(order.order_level, req.order_id, order.weed_list)
    await client.send_packet(MSG_PACK_CHANGE, change.SerializeToString())
    upd = P.HomeUpdateS2C()
    upd.garden_order.CopyFrom(data)
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, False)
    popup = P.NotifyRewardsS2C()
    for item, n in rewards:
        popup.items.add(conf_id=item, number=n)
    await client.send_packet(0x0404, popup.SerializeToString())
    coins = sum(n for item, n in rewards if item == HOME_COIN)
    if coins:
        await pvp.HOOKS["mission_event"](client, EARN_CURRENCY, coins, (HOME_COIN,))
    await client.send_packet(MSG_ORDER, P.HomeGardenOrderFinishS2C(order_id=req.order_id).SerializeToString())
    log.info(f"  GARDEN order {req.order_id} delivered: {rewards}")


def crops_update(client):
    """FurniturePackItemChangeS2C with this player's crop totals, for the login."""
    store = _garden(entry(client)).get("crops") or {}
    if not store:
        return None
    crops = _crops(client)
    out = P.FurniturePackItemChangeS2C()
    for conf in store:
        out.gain_items.add().CopyFrom(crops[int(conf)])
    return out


def garden_update(client):
    """The lands and machines this player changed, for the login (the baseline HomeInfoS2C has the capture's)."""
    g = _garden(entry(client))
    if not g["lands"] and not g["machines"]:
        return None
    homes = furnitures(client)
    upd = P.HomeUpdateS2C()
    for inst in list(g["machines"]) + list(g["lands"]):
        if int(inst) in homes:
            upd.furnitures.add().CopyFrom(homes[int(inst)])
    return upd


def on_login(client) -> list[tuple[int, bytes]]:
    """Brings the schedules up to date and tells the client (the baseline HomeInfoS2C is the capture's)."""
    d = entry(client)
    advance(d)
    pvp.save()
    out = [(MSG_UPDATE, update(d).SerializeToString())] if d["schedules"] else []
    garden = garden_update(client)
    data = orders(client, int(time.time()))
    if "orders" in _garden(d):
        garden = garden or P.HomeUpdateS2C()
        garden.garden_order.CopyFrom(data)
    if garden is not None:
        out.append((MSG_UPDATE, garden.SerializeToString()))
    crops = crops_update(client)
    if crops is not None:
        out.append((MSG_PACK_CHANGE, crops.SerializeToString()))
    return out


# --- rooms: upgrading an indoor area --------------------------------------------------------------------
# 0x2306 HomeIndoorAreaLevelUpC2S{1=area_id 2=deletes} (HomesteadMessageHandler::RequestIndoorAreaUpgrade).
# The client (OnReceivedAreaUpgrade) removes the furniture in `deletes`, sets the area to level_id, takes
# `comfort` as the new comfort and opens HomesteadUpgradeSuccessView for level_id. Before, the server only
# sent an empty reply (level_id 0): the success banner stayed on screen (Emre, new account, lobby Lv.1 -> 2).
# Rule (homesteadindoorlevel): the next row of the area (Level + 1) needs base level >= HomesteadLevel and
# costs UnlockSource [8, item, 16, number]. Assumptions (no capture): comfort does not change, an area
# without a row in the HomeInfo starts at level 0 (unlocking = level 1), refused -> err FAILED.
MSG_AREA_UP = 0x2306
AREA_LEVELS = {(r["AreaID"], r["Level"]): r for r in pvp._cfg("homesteadindoorlevel")}


def _base_info(client) -> "P.HomeInfoS2C":
    """The baseline HomeInfoS2C with this account's base level and comfort (see handle_home_level_up)."""
    info = P.HomeInfoS2C()
    try:
        info.ParseFromString(client.account.b.pb("0x2301"))
    except Exception:
        pass
    d = entry(client)
    if "level" in d:
        info.base_info.level = d["level"]
        info.base_info.comfort.total = d["comfort"]
    if "expansion" in d:
        info.base_info.expansion_times = d["expansion"]
    return info


# --- outdoor: expansion ------------------------------------------------------------------------------------
# 0x2305 HomeOutdoorSpaceExpandC2S{} -> HomeOutdoorSpaceExpandS2C{err, expansion_times}. Had no handler (empty
# reply = expansion_times 0). From the client (HomesteadExpandViewLogic::OnConfirmEvent): it asks only when the
# base level reaches LevelLimit of homesteadoutdoorexpansion row expansion_times + 1 and the player has that
# row's ExpansionConsume; OnReceivedOutdoorExpansion takes expansion_times and grows the outdoor scene.
# Assumptions (no capture): that row's ExpansionConsume is taken; refused -> err FAILED, nothing taken.
MSG_EXPAND = 0x2305
EXPANSIONS = {r.get("ID", 0): r for r in pvp._cfg("homesteadoutdoorexpansion")}


async def handle_outdoor_expand(client, pb: bytes):
    import wire
    info = _base_info(client)
    times, level = info.base_info.expansion_times, info.base_info.level
    row = EXPANSIONS.get(times + 1)
    cost = (row.get("ExpansionConsume") or []) if row else []
    item, number = (cost[1], cost[3]) if isinstance(cost, list) and len(cost) >= 4 else (0, 0)
    acc = client.account
    if row is None or level < (row.get("LevelLimit") or 0) or (item and acc.get_currency(item) < number):
        log.info(f"  HOME outdoor expansion {times} -> {times + 1}: refused (base level {level})")
        await client.send_packet(MSG_EXPAND, P.HomeOutdoorSpaceExpandS2C(err=FAILED, expansion_times=times).SerializeToString())
        return
    if item:
        entry_ = acc.set_currency(item, acc.get_currency(item) - number)
        await client.send_packet(0x0405, wire.build([(1, 2, entry_)]))
    entry(client)["expansion"] = times + 1
    acc.save()
    pvp.save()
    await client.send_packet(MSG_EXPAND, P.HomeOutdoorSpaceExpandS2C(expansion_times=times + 1).SerializeToString())
    log.info(f"  HOME outdoor expansion {times} -> {times + 1} for {number} x {item}")


# --- base: level up ----------------------------------------------------------------------------------------
# 0x2303 HomeLevelUpC2S{} (HomesteadMessageHandler::RequestHomeLevelUp, upgrade pop-up with isBaseUpgrade).
# The client (OnReceivedHomeLevelUp) takes level and comfort from HomeLevelUpS2C{err, level, comfort} and opens
# HomesteadUpgradeSuccessView for that level. Before, the server sent an empty reply (level 0): the success
# banner showed a room text and could not be closed, and the base stayed at its level (Emre 28.11.2026).
# From the client: the button only asks when HomesteadBaseData.NextLevelConfig (homesteadlevel row level + 1,
# none above the highest level) exists, and the pop-up shows that row's LevelUpConsume [8, item, 16, number] as
# the cost. Assumptions (no capture): the cost is that row's LevelUpConsume; the comfort is the new level's
# BasicComfort plus the furniture comfort the account had (comfort total minus the old BasicComfort);
# not enough of the item or the highest level -> err FAILED, nothing taken.
MSG_LEVEL_UP = 0x2303
HOME_LEVELS = {r["ID"]: r for r in pvp._cfg("homesteadlevel")}


async def handle_home_level_up(client, pb: bytes):
    import wire
    info = _base_info(client)
    cur, total = info.base_info.level, info.base_info.comfort.total
    row = HOME_LEVELS.get(cur + 1)
    cost = (row.get("LevelUpConsume") or []) if row else []
    item, number = (cost[1], cost[3]) if len(cost) >= 4 else (0, 0)
    acc = client.account
    if row is None or (item and acc.get_currency(item) < number):
        log.info(f"  HOME base level {cur} -> {cur + 1}: refused")
        await client.send_packet(MSG_LEVEL_UP, P.HomeLevelUpS2C(err=FAILED).SerializeToString())
        return
    if item:
        entry_ = acc.set_currency(item, acc.get_currency(item) - number)
        await client.send_packet(0x0405, wire.build([(1, 2, entry_)]))
    old_basic = (HOME_LEVELS.get(cur) or {}).get("BasicComfort") or 0
    d = entry(client)
    d["level"] = cur + 1
    d["comfort"] = max(total - old_basic, 0) + (row.get("BasicComfort") or 0)
    acc.save()
    pvp.save()
    out = P.HomeLevelUpS2C(level=d["level"])
    out.comfort.CopyFrom(info.base_info.comfort)
    out.comfort.total = d["comfort"]
    await client.send_packet(MSG_LEVEL_UP, out.SerializeToString())
    log.info(f"  HOME base level {cur} -> {d['level']} for {number} x {item}, comfort {total} -> {d['comfort']}")


def areas(client) -> dict:
    """{area_id: level_id} of this player's rooms (from the baseline HomeInfoS2C, then the upgrades)."""
    d = entry(client)
    if "areas" not in d:
        d["areas"] = {str(a.level_id // 100): a.level_id for a in _base_info(client).areas_in}
    return d["areas"]


def patch_info(client, body: bytes) -> bytes:
    """The login HomeInfoS2C with this player's room levels and comfort rewards."""
    d = entry(client)
    if not any(k in d for k in ("areas", "comfort_next", "comfort_rewarded", "level", "expansion")):
        return body
    info = P.HomeInfoS2C.FromString(body)
    if "level" in d:  # base level and comfort after a base level up
        info.base_info.level = d["level"]
        info.base_info.comfort.total = d["comfort"]
    if "areas" in d:
        del info.areas_in[:]
        for level_id in sorted(d["areas"].values()):
            info.areas_in.add(level_id=level_id)
    if "expansion" in d:  # outdoor expansions of this account
        info.base_info.expansion_times = d["expansion"]
    if "comfort_next" in d:  # the daily comfort pack this account already took
        info.base_info.comfort_daily_reward_unix = d["comfort_next"]
    if "comfort_rewarded" in d:  # the comfort level packs this account took (bit mask)
        info.base_info.comfort_level_rewarded = d["comfort_rewarded"]
    return info.SerializeToString()


async def handle_area_level_up(client, pb: bytes):
    import wire
    req = P.HomeIndoorAreaLevelUpC2S.FromString(pb)
    info = _base_info(client)
    have = areas(client)
    cur = have.get(str(req.area_id), 0) % 100
    row = AREA_LEVELS.get((req.area_id, cur + 1))
    cost = row.get("UnlockSource") or [] if row else []
    item, number = (cost[1], cost[3]) if len(cost) >= 4 else (0, 0)
    acc = client.account
    if row is None or info.base_info.level < (row.get("HomesteadLevel") or 0) \
            or (item and acc.get_currency(item) < number):
        log.info(f"  HOME area {req.area_id} level {cur} -> {cur + 1}: refused")
        await client.send_packet(MSG_AREA_UP, P.HomeIndoorAreaLevelUpS2C(err=FAILED).SerializeToString())
        return
    if item:
        entry_ = acc.set_currency(item, acc.get_currency(item) - number)
        await client.send_packet(0x0405, wire.build([(1, 2, entry_)]))
    have[str(req.area_id)] = row["ID"]
    acc.save()
    pvp.save()
    out = P.HomeIndoorAreaLevelUpS2C(deletes=list(req.deletes), level_id=row["ID"])
    out.comfort.CopyFrom(info.base_info.comfort)
    await client.send_packet(MSG_AREA_UP, out.SerializeToString())
    log.info(f"  HOME area {req.area_id} -> level {cur + 1} ({row['ID']}) for {number} x {item}")


HANDLERS = {MSG_REFRESH: handle_refresh, MSG_COMFORT: handle_comfort_reward, MSG_COLLECT: handle_collect,
            MSG_COLLECT_ONE: handle_collect_one,
            MSG_LAND_WEED: handle_land_weed, MSG_REPAIR: handle_repair, MSG_ORDER: handle_order,
            MSG_AREA_UP: handle_area_level_up, MSG_LEVEL_UP: handle_home_level_up,
            MSG_EXPAND: handle_outdoor_expand, MSG_PLAN: handle_plan}
