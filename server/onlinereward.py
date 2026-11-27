"""
Cumulative Online Rewards (local activity type 3, ACTIVITY_ONLINE_REWARD), per player.

Before, the login replayed the owner's captured activity (online_start_sec in the past, so every stage looked
reached) and 0x2803 LocalActivityReward was answered with the captured reply: nothing was booked, the stages
stayed claimable but never got claimed.

Client (hotfix_il_full.txt, Activity.OnlineReward.*): OnlineRewardViewLogic.OnRewardListEvent sends
0x2803 LocalActivityRewardC2S {type 3, data = ActivityOnlineRewardReward {gear -1}} (every available stage at
once); online time = history_online_sec + (server time - online_start_sec) (CalcCurrOnlineTime); stages are
onlinerewardsconf rows of the curRewardLev band (FilterCfgsByAccountLevel); claimed = gear_reward_got.

Evidenced (decoded_tshark.json 828 -> 2805-2809, a Garena claim of stages 4-6 with gear -1):
  0x2802 LocalActivityUpdateS2C {type 3, data with gear_reward_got 1,2,3 + 6,4,5}, 0x0802 item 102000014 x1,
  0x0405 currencies 102 / 100, 0x0404 reward popup (102 x15000, 100 x25000, 102000014 x1), 0x2803 {type 3};
  the daily reset (login data of 6 captures): the first login of a day has history 0, no stages, and
  online_start_sec = last_reset_sec = login time (wcy2611); a session running over 06:00 UTC restarts at
  06:00:00 (decrypted_atsumu2 last_reset 1795068000); online_start_sec is the login time of the session
  (garena4: = server time of the login).
Assumed: online time of a session is booked to history at logout; a request for one gear (>= 1) claims only
that gear; nothing claimable -> err FAILED with no update; the stage order in gear_reward_got is ascending
(Garena's 6, 4, 5 has no visible rule).
State: pvp.json "online" {gid: {history, start, gears, reset, lev}}.
"""
import time

import pvp

TYPE = 3
MSG_UPDATE, MSG_REWARD = 0x2802, 0x2803
RESET_HOUR_UTC = 6


def _P():
    import matches
    return matches.P


def day_start(now: float) -> int:
    """The last 06:00 UTC at or before now."""
    t = int(now) - RESET_HOUR_UTC * 3600
    return t - t % 86400 + RESET_HOUR_UTC * 3600


def _rows() -> list[dict]:
    return [r for r in pvp._cfg("onlinerewardsconf") if r.get("GearNumber")]


def stages(lev: int) -> dict[int, dict]:
    """gear -> row of the level band the client shows (highest LevelLimt <= lev, else the lowest band)."""
    rows = _rows()
    bands = sorted({r.get("LevelLimt") or 0 for r in rows})
    band = max([b for b in bands if b <= lev], default=bands[0] if bands else 0)
    return {r["GearNumber"]: r for r in rows if (r.get("LevelLimt") or 0) == band}


def reward_items(row: dict) -> list[tuple[int, int]]:
    import weekly
    return [(r[8], r[16]) for r in weekly._pairs(row.get("Reward")) if 8 in r and 16 in r]


def state(gid: int, acc=None) -> dict:
    st = pvp.state().setdefault("online", {})
    d = st.get(str(gid))
    if d is None:
        d = st[str(gid)] = {"history": 0, "start": 0, "gears": [], "reset": 0, "lev": 60}
        if acc is not None:
            base = baseline(acc)
            if base is not None:
                d["lev"] = base.cur_reward_lev or 60
    return d


def baseline(acc):
    """The account's own type-3 data from its login data, or None."""
    import wire
    P = _P()
    for m in acc.b.sync:
        if m["msg_id"].upper() == "0X2801":
            info = P.LocalActivityInfoS2C.FromString(wire.body_to_pb(bytes.fromhex(m["body_hex"]), m["flags"]))
            for a in info.activity_list:
                if a.type == TYPE:
                    return P.ActivityOnlineRewardData.FromString(a.data)
    return None


def _roll(d: dict, now: float):
    """Daily reset inside a session: a session that ran over 06:00 UTC counts from 06:00:00 with no stages
    (decrypted_atsumu2: last_reset_sec = 06:00:00)."""
    start = day_start(now)
    if d["reset"] < start:
        d.update(history=0, gears=[], reset=start)
        if d["start"] and d["start"] < start:
            d["start"] = start


def login(gid: int, acc, now: float | None = None):
    now = int(time.time() if now is None else now)
    d = state(gid, acc)
    if d["reset"] < day_start(now):  # first login of the day: everything starts now (wcy2611)
        d.update(history=0, gears=[], reset=now)
    d["start"] = now
    pvp.save()


def logout(gid: int, now: float | None = None):
    now = int(time.time() if now is None else now)
    d = pvp.state().get("online", {}).get(str(gid))
    if not d or not d["start"]:
        return
    _roll(d, now)
    d["history"] += max(0, now - d["start"])
    d["start"] = 0
    pvp.save()


def online_seconds(d: dict, now: float) -> int:
    return d["history"] + (max(0, int(now) - d["start"]) if d["start"] else 0)


def data(d: dict) -> bytes:
    P = _P()
    return P.ActivityOnlineRewardData(history_online_sec=d["history"], online_start_sec=d["start"],
                                      gear_reward_got=d["gears"], cur_reward_lev=d["lev"],
                                      last_reset_sec=d["reset"]).SerializeToString()


def patch_info(gid: int, acc, body: bytes) -> bytes:
    """The baseline's LocalActivityInfoS2C with this player's type-3 data."""
    P = _P()
    info = P.LocalActivityInfoS2C.FromString(body)
    d = state(gid, acc)
    for a in info.activity_list:
        if a.type == TYPE:
            a.data = data(d)
    return info.SerializeToString()


def claim(d: dict, gear: int, now: float) -> list[int]:
    """Stages claimed now (gear -1 = every reached one)."""
    _roll(d, now)
    secs = online_seconds(d, now)
    rows = stages(d["lev"])
    want = sorted(rows) if gear < 0 else [gear]
    got = [g for g in want if g in rows and g not in d["gears"] and (rows[g].get("OnlineTime") or 0) <= secs]
    d["gears"] += got
    return got


async def handle_reward(client, req) -> bool:
    """0x2803 for type 3. Returns False when the request is not for this activity."""
    if req.type != TYPE:
        return False
    P = _P()
    gid, acc = int(client.gid), client.account
    d = state(gid, acc)
    try:
        gear = P.ActivityOnlineRewardReward.FromString(req.data).gear
    except Exception:
        gear = -1
    got = claim(d, gear, time.time())
    if not got:
        await client.send_packet(MSG_REWARD, P.LocalActivityRewardS2C(type=TYPE, err=P.FAILED).SerializeToString())
        return True
    pvp.save()
    rows = stages(d["lev"])
    rewards = [it for g in got for it in reward_items(rows[g])]
    upd = P.LocalActivityUpdateS2C()
    upd.activity_list.add(type=TYPE, start_time=_start_time(acc), data=data(d))
    await client.send_packet(MSG_UPDATE, upd.SerializeToString())
    import gameconfig
    await pvp.HOOKS["grant_rewards"](client, gameconfig.open_rewards(rewards), True)
    await client.send_packet(MSG_REWARD, P.LocalActivityRewardS2C(type=TYPE).SerializeToString())
    return True


def _start_time(acc) -> int:
    import wire
    P = _P()
    for m in acc.b.sync:
        if m["msg_id"].upper() == "0X2801":
            info = P.LocalActivityInfoS2C.FromString(wire.body_to_pb(bytes.fromhex(m["body_hex"]), m["flags"]))
            for a in info.activity_list:
                if a.type == TYPE:
                    return a.start_time
    return 0
