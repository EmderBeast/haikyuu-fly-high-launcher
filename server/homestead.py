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


def on_login(client) -> list[tuple[int, bytes]]:
    """Brings the schedules up to date and tells the client (the baseline HomeInfoS2C is the capture's)."""
    d = entry(client)
    advance(d)
    pvp.save()
    return [(MSG_UPDATE, update(d).SerializeToString())] if d["schedules"] else []


HANDLERS = {MSG_REFRESH: handle_refresh, MSG_COMFORT: handle_comfort_reward, MSG_COLLECT: handle_collect}
