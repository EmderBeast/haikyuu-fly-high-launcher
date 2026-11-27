"""
Training supply (local activity type 8, ACTIVITY_TRAIN_SUPPLY): a weekend reward on Saturday, Sunday and
Monday, per player.

Before, the login replayed the owner's captured state and 0x2803 got the captured reply {type 3}: the day's
supply could not be claimed and nothing was booked.

Client (hotfix_il_full.txt, Activity.TrainSupplyReward.*): TrainSupplyRewardViewLogic sends 0x2803 {type 8,
data = an empty ActivityTrainSupplyReward}; TrainSupplyRewardData.UpdateTrainSupplyRewardData: the day is
(server time - 6 h) as weekday 1..7 (Sunday = 7), the rows are trainsupply.json of the level band
(Level [from, to] around curLevel), a row is claimable on its RewardTime day when bit (1 << RewardTime) of
is_reward is not set.
Captured login data (8 captures): is_reward 130 (Sunday + Monday claimed), 64 (Saturday), 0 (new week);
last_refresh_unix is a Tuesday 06:00 UTC (17.11., 24.11., 01.12.2026; the next one in the recent captures).
Assumed (no capture of this claim): the reply works like the captured claims of types 1 and 3 (0x2802 update,
item/currency changes, 0x0404 pop-up, 0x2803 {type 8}); the week (is_reward) starts again on Tuesday 06:00 UTC;
the day is counted from 06:00 UTC (the client counts in the device's local time); not the day or claimed ->
err FAILED.
State: pvp.json "trainsupply" {gid: {week, bits}}.
"""
import time

import pvp
import onlinereward

TYPE = 8
TUESDAY = 1  # weekday() of Tuesday


def _P():
    import matches
    return matches.P


def week_start(now: float) -> int:
    """The last Tuesday 06:00 UTC at or before now."""
    day = onlinereward.day_start(now)
    wd = time.gmtime(day).tm_wday  # Monday 0
    return day - ((wd - TUESDAY) % 7) * 86400


def weekday(now: float) -> int:
    """1..7 (Sunday 7) of the game day (from 06:00 UTC)."""
    return time.gmtime(onlinereward.day_start(now)).tm_wday + 1


def state(gid: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    st = pvp.state().setdefault("trainsupply", {})
    d = st.setdefault(str(gid), {"week": 0, "bits": 0})
    w = week_start(now)
    if d["week"] != w:
        d.update(week=w, bits=0)
    return d


def rows(level: int) -> list[dict]:
    import weekly
    out = []
    for r in pvp._cfg("trainsupply"):
        band = weekly._pairs(r.get("Level"))
        lo, hi = (band[0].get(8, 0), band[0].get(16, 999)) if band else (0, 999)
        if lo <= level <= hi:
            out.append(r)
    return out


def reward_items(row: dict) -> list[tuple[int, int]]:
    import weekly
    return [(r[8], r[16]) for r in weekly._pairs(row.get("RewardItem")) if 8 in r and 16 in r]


def data(d: dict, level: int, now: float | None = None) -> bytes:
    now = time.time() if now is None else now
    return _P().ActivityTrainSupplyData(last_refresh_unix=week_start(now) + 7 * 86400, is_reward=d["bits"],
                                        cur_level=level).SerializeToString()


def patch_info(gid: int, info, level: int) -> None:
    d = state(gid)
    for a in info.activity_list:
        if a.type == TYPE:
            a.data = data(d, level)


def claim(d: dict, level: int, now: float) -> list[tuple[int, int]] | None:
    day = weekday(now)
    row = next((r for r in rows(level) if r.get("RewardTime") == day), None)
    if row is None or d["bits"] & (1 << day):
        return None
    d["bits"] |= 1 << day
    return reward_items(row)


async def handle_reward(client, req) -> bool:
    if req.type != TYPE:
        return False
    P = _P()
    gid, acc = int(client.gid), client.account
    level = acc.level
    d = state(gid)
    rewards = claim(d, level, time.time())
    if rewards is None:
        await client.send_packet(onlinereward.MSG_REWARD, P.LocalActivityRewardS2C(type=TYPE, err=P.FAILED).SerializeToString())
        return True
    pvp.save()
    upd = P.LocalActivityUpdateS2C()
    upd.activity_list.add(type=TYPE, start_time=_start_time(acc), data=data(d, level))
    await client.send_packet(onlinereward.MSG_UPDATE, upd.SerializeToString())
    import gameconfig
    await pvp.HOOKS["grant_rewards"](client, gameconfig.open_rewards(rewards), True)
    await client.send_packet(onlinereward.MSG_REWARD, P.LocalActivityRewardS2C(type=TYPE).SerializeToString())
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
