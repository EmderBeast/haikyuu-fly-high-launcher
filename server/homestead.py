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
Not served here (docs/garena-abgleich.md): planning players (0x2318), collecting one schedule (0x2308)
or one player's reward (0x2316).
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


# --- comfort: the daily reward ----------------------------------------------------------------------
# 0x2315 HomeComfortRewardC2S{level = -1}. Garena captures (decrypted_3 / decrypted_2_reconnect, one claim):
#   - the account had comfort 5085 = level 20 of homesteadcomfort (ComfortNumber 5000); it got that row's
#     DailyGiftPack: 227 x3900, 117 x48 and the box 102000011, which opened at once (popup: 102 x2500, one
#     of the entries of its gift 1074)
#   - order: currency change, NotifyRewardsS2C, TaskChangeS2C (weekly mission 6000015 "earn 117": +48),
#     then HomeComfortRewardS2C{level = -1, comfort_daily_reward_unix = the next 06:00}
# Assumptions: asked again before that time, the reply only repeats the time; the comfort value is the
# account's baseline value (nothing on this server changes it yet). The level rewards (level >= 1) are
# not served: no capture of such a claim.
MSG_COMFORT = 0x2315
DAILY_REWARD, HOME_COIN, EARN_CURRENCY = -1, 117, 80104
COMFORT = sorted(pvp._cfg("homesteadcomfort"), key=lambda r: r.get("ComfortNumber") or 0)


def comfort_row(total: int) -> dict | None:
    """The highest comfort level reached with that comfort value."""
    reached = [r for r in COMFORT if (r.get("ComfortNumber") or 0) <= total]
    return reached[-1] if reached else None


def daily_pack(row: dict, rng=None) -> list[tuple[int, int]]:
    """DailyGiftPack of the level, boxes that open by themselves replaced by what they hold."""
    import monthcard
    import pack
    import random
    won: dict[int, int] = {}
    for item, n in monthcard.pairs(row.get("DailyGiftPack")):
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
    if req.level != DAILY_REWARD:
        await pvp.HOOKS["generic"](client, pb, MSG_COMFORT)
        return
    info = P.HomeInfoS2C()
    try:
        info.ParseFromString(client.account.b.pb("0x2301"))
    except Exception:
        pass
    d = entry(client)
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
SOW, UPROOT, HARVEST = 0, 1, 3
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
        if MACHINES.get(m.conf_id, {}).get("Type") != 1:
            continue
        x, z, w, h = cover_rect(m)
        if (land.coord.x < x + w and land.coord.x + MACHINE_SIZE > x
                and land.coord.z < z + h and land.coord.z + MACHINE_SIZE > z):
            return m
    return None


def gain_times(weed: dict, start: int, machine) -> list:
    ripe = (weed.get("RipeningTime") or [0])[-1] * 3600
    if machine is not None:
        ripe = ripe * 10000 // (10000 + (MACHINES.get(machine.conf_id, {}).get("Funtion") or 0))
    rounds = weed.get("GainNum")
    n = len(rounds) if isinstance(rounds, list) else 1
    return [start + ripe * (i + 1) for i in range(n)]


async def handle_land_weed(client, pb: bytes, now: float | None = None):
    req = P.HomeGardenLandWeedC2S.FromString(pb)
    now = int(time.time() if now is None else now)
    if req.status == HARVEST:
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
        m.machine.start_time, m.machine.update_time = now, until
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
# 0x231A with status 3 (HomesteadMainViewLogic::OnAllHarvestBtnEvent: GardenUtility.GetHarvestableInstIds,
# weed_id 0). Garena capture decrypted_1 0076-0079 (12 lands of 4002004, GainNum 8, GainItemID 920000104):
#   - FurniturePackItemChangeS2C{2=gain_items: the crop with its new total (1248 + 96 = 1344)}
#   - NotifyRewardsS2C with the crop x96 (12 x GainNum), then HomeUpdateS2C with the lands, now empty
#     (land only keeps `decrease`), then the reply {status 3, the lands}
# Crops live in the furniture pack (FurniturePackItemInfoS2C.gain_items at login, the baseline's).
# Assumptions: a land gives GainNum[i] for every harvest time it has reached and not yet given
# (gain_round counts them); it is emptied after the last one, otherwise it keeps growing; lands with
# nothing ripe are left out; nothing ripe at all -> FAILED.
MSG_PACK_CHANGE = 0x2202


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


async def harvest(client, req, now: int):
    homes = furnitures(client)
    g = _garden(entry(client))
    lands, won = [], {}
    for inst in req.inst_id:
        f = homes.get(inst)
        if f is None or f.conf_id != LAND_CONF or not f.land.weed_id:
            continue
        weed = WEEDS.get(f.land.weed_id, {})
        due = [i for i, t in enumerate(f.land.gain_times) if t <= now and i >= f.land.gain_round]
        if not due:
            continue
        crop = weed.get("GainItemID")
        won[crop] = won.get(crop, 0) + sum(_gain_num(weed, i) for i in due)
        if due[-1] + 1 >= len(f.land.gain_times):
            decrease = f.land.decrease
            f.land.Clear()
            f.land.decrease = decrease
        else:
            f.land.gain_round = due[-1] + 1
        g["lands"][str(inst)] = f.land.SerializeToString().hex()
        lands.append(f)
    if not lands:
        log.info(f"  GARDEN harvest {list(req.inst_id)}: nothing ripe")
        await client.send_packet(MSG_LAND_WEED, P.HomeGardenLandWeedS2C(err=FAILED, status=HARVEST).SerializeToString())
        return
    change = P.FurniturePackItemChangeS2C()
    popup = P.NotifyRewardsS2C()
    for crop, n in won.items():
        change.gain_items.add().CopyFrom(add_crop(client, crop, n, now))
        popup.items.add(conf_id=crop, number=n)
    pvp.save()
    await client.send_packet(MSG_PACK_CHANGE, change.SerializeToString())
    await client.send_packet(0x0404, popup.SerializeToString())
    upd = P.HomeUpdateS2C()
    out = P.HomeGardenLandWeedS2C(status=HARVEST)
    for f in lands:
        upd.furnitures.add().CopyFrom(f)
        out.furniture.add().CopyFrom(f)
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    await client.send_packet(MSG_LAND_WEED, out.SerializeToString())
    log.info(f"  GARDEN harvest {len(lands)} land(s): {won}")


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
    if garden is not None:
        out.append((MSG_UPDATE, garden.SerializeToString()))
    crops = crops_update(client)
    if crops is not None:
        out.append((MSG_PACK_CHANGE, crops.SerializeToString()))
    return out


HANDLERS = {MSG_REFRESH: handle_refresh, MSG_COMFORT: handle_comfort_reward, MSG_COLLECT: handle_collect,
            MSG_LAND_WEED: handle_land_weed}
