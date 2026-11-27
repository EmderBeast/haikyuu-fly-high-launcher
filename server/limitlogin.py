"""
Limited-time login stamina (local activity type 1, ACTIVITY_LIMIT_LOGIN): noon and evening stamina, per player.

Before, the login replayed the owner's captured states (noon and night GOT) and 0x2803 got the captured reply
{type 3}: nothing could be claimed and nothing was booked.

Client (hotfix_il_full.txt, Activity.LimitTimeLogin.*): LimitTimeLoginViewLogic.SendRewardMsg sends 0x2803
{type 1, data = ActivityLimitLoginReward {reward_type 0 noon / 1 night, is_reward_back}}; "GetBtn" claims,
"ReissueBtn" claims a missed one with is_reward_back and costs limitlogin.Cost (108 x20, checked by the client).
The client decides the windows itself in the device's local time (TimeStampToLocalDateTime, limitlogin
NoonTime 11:00-24:00, NightTime 19:00-24:00, a day running from 06:00 to 06:00, OnSixClockReset); state 2 =
GOT. Reward = limitlogin.Reward (105 stamina x60).
Evidenced (decoded_tshark.json 830 -> 2811-2814, a Garena claim of the night stamina): request {1=1};
0x2802 update {type 1, data {noon GOT, night GOT}}, 0x0405 stamina 105 +60, 0x0404 popup 105 x60, 0x2803
{type 1}; the login data of the captures: noon/night GOT after claiming, nothing at the first login of a day
(wcy2611).
Assumed: the day changes at 06:00 UTC as for the online rewards (the client counts in local time); the server
does not check the windows (it cannot know the device's time zone), only that the slot is not claimed yet; a
reissue (is_reward_back) takes 108 x20 first, refused (FAILED) without it.
State: pvp.json "limitlogin" {gid: {day, noon, night}}.
"""
import time

import pvp
import onlinereward

TYPE = 1
GOT = 2


def _P():
    import matches
    return matches.P


def _row() -> dict:
    return next(iter(pvp._cfg("limitlogin")), {})


def _pair(spec) -> tuple[int, int] | None:
    import weekly
    p = weekly._pairs(spec)
    return (p[0][8], p[0][16]) if p and 8 in p[0] and 16 in p[0] else None


def state(gid: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    st = pvp.state().setdefault("limitlogin", {})
    d = st.setdefault(str(gid), {"day": 0, "noon": 0, "night": 0})
    day = onlinereward.day_start(now)
    if d["day"] != day:  # a new day: both open again
        d.update(day=day, noon=0, night=0)
    return d


def data(d: dict) -> bytes:
    return _P().ActivityLimitLoginData(noon_time_state=d["noon"], night_time_state=d["night"]).SerializeToString()


def patch_info(gid: int, info) -> None:
    """Puts this player's type-1 data into a LocalActivityInfoS2C (in place)."""
    d = state(gid)
    for a in info.activity_list:
        if a.type == TYPE:
            a.data = data(d)


def claim(d: dict, slot: int) -> bool:
    key = "noon" if slot == 0 else "night" if slot == 1 else None
    if key is None or d[key] == GOT:
        return False
    d[key] = GOT
    return True


async def handle_reward(client, req) -> bool:
    if req.type != TYPE:
        return False
    P = _P()
    gid, acc = int(client.gid), client.account
    d = state(gid)
    try:
        r = P.ActivityLimitLoginReward.FromString(req.data)
    except Exception:
        r = P.ActivityLimitLoginReward()
    key = "noon" if r.reward_type == 0 else "night"
    cost = _pair(_row().get("Cost")) if r.is_reward_back else None
    ok = r.reward_type in (0, 1) and d[key] != GOT and (cost is None or acc.get_currency(cost[0]) >= cost[1])
    if not ok:
        await client.send_packet(onlinereward.MSG_REWARD, P.LocalActivityRewardS2C(type=TYPE, err=P.FAILED).SerializeToString())
        return True
    if cost:  # reissue of a missed slot: the cost first (0x0405)
        import wire
        entry = acc.set_currency(cost[0], acc.get_currency(cost[0]) - cost[1])
        acc.save()
        from accounts import MSG_CURRENCY_CHANGE
        await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, entry)]))
    claim(d, r.reward_type)
    pvp.save()
    upd = P.LocalActivityUpdateS2C()
    upd.activity_list.add(type=TYPE, start_time=_start_time(acc), data=data(d))
    await client.send_packet(onlinereward.MSG_UPDATE, upd.SerializeToString())
    reward = _pair(_row().get("Reward"))
    if reward:
        await pvp.HOOKS["grant_rewards"](client, [reward], True)
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
