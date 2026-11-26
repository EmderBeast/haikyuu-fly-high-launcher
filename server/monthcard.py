"""
Month card / quarter card: the daily reward. 0x2003 RechargeCardRewardC2S{limit_group[]}.

No capture holds this exchange (the recorded accounts had claimed already or had no running card), so
this follows the client's code (decoded/analysis/hotfix_il_full.txt) and the tables:
  - the client asks by itself once per session when the home screen comes up (BannerShowEventData ->
    MonthCardGiftUtils::GetMonthCardDayReward) and from the two buttons in the shop; limit_group holds
    the groups whose red point is on: 104011 (month card, mallrecharge Type 5) and / or 104021 (quarter
    card, Type 7)
  - a card is running while start_time + total_day * 86400 > now, and its reward can be taken when now
    has reached the first 06:00 (game time zone) after last_receive_time, i.e. 06:00 on the date of
    last_receive_time + 18 h (MonthCardViewUtils::ChickCanGetMonthCardRewardPoint,
    RechargeLimitBuyData::GetLastRewardTime)
  - the reward is the row's EveryDayItemReward: month 108 x90, quarter 108 x100 and 105000045 x1
  - the client learns that it has been taken only from a RechargeInfoS2C (0x2001) with all = false,
    which it merges by limit_group; RechargeCardRewardS2C itself is only checked for an error; a
    NotifyRewardsS2C with origin ORIGIN_CARD opens the card's own reward window
  - the captured logins show last_receive_time as the server's time of the claim (e.g. 06:01:21 on the
    day of the recording) and nothing else moving with it

Before this the server answered with an empty message and changed nothing, so the client asked again
at every login (183 times in the server log).

Assumptions (the exchange itself was never recorded):
  - order: RechargeInfoS2C, the item change, NotifyRewardsS2C, then the reply - as Garena does for the
    free packs of the shop (decrypted_3, messages 0155-0170: info, item change, popup, reply)
  - both cards in one request give one reward window with both rewards
  - a group that cannot be claimed is skipped; the reply has no error then either
  - the game time zone is the one the server names at login (UTC+1)
"""
import calendar
import logging
import time

import matches
import pvp

P = matches.P
log = logging.getLogger("hfh")

MSG_INFO, MSG_CARD_REWARD, MSG_NOTIFY_REWARDS = 0x2001, 0x2003, 0x0404
MONTH, QUARTER = 104011, 104021
TIME_ZONE_HOURS = 1  # LoginBySessionS2C.time_zone as server.py sends it
RESET_HOUR = 6


def pairs(value) -> list[tuple[int, int]]:
    """A reward column of mallrecharge: raw key/value pairs [8, item, 16, number, ...] -> [(item, number)]."""
    flat = []

    def walk(v):
        for x in v if isinstance(v, list) else [v]:
            walk(x) if isinstance(x, list) else flat.append(x)
    walk(value or [])
    return [(flat[i + 1], flat[i + 3]) for i in range(0, len(flat) - 3, 4) if flat[i] == 8 and flat[i + 2] == 16]


def _daily(group: int) -> list[tuple[int, int]]:
    """EveryDayItemReward of the card."""
    row = next((r for r in pvp._cfg("mallrecharge") if r.get("LimitGroup") == group), {})
    return pairs(row.get("EveryDayItemReward"))


REWARDS = {MONTH: _daily(MONTH), QUARTER: _daily(QUARTER)}


def boundary(last_receive: int) -> int:
    """The first 06:00 (game time zone) after a claim: 06:00 on the date of last_receive + 18 h."""
    tz = TIME_ZONE_HOURS * 3600
    d = time.gmtime(last_receive + 18 * 3600 + tz)
    return calendar.timegm((d.tm_year, d.tm_mon, d.tm_mday, RESET_HOUR, 0, 0)) - tz


def limits(client) -> dict[int, "P.RechargeLimitData"]:
    """The account's card entries: the baseline's, with the claims made on this server."""
    info = P.RechargeInfoS2C()
    try:
        info.ParseFromString(client.account.b.pb("0x2001"))
    except Exception:
        pass
    mine = pvp.state().get("monthcard", {}).get(str(client.gid), {})
    out = {}
    for lim in info.limits:
        if lim.limit_group in REWARDS:
            lim.last_receive_time = max(lim.last_receive_time, mine.get(str(lim.limit_group), 0))
            out[lim.limit_group] = lim
    return out


def claimable(lim, now: int) -> bool:
    if lim.start_time + lim.total_day * 86400 <= now:
        return False  # the card has run out
    return now >= boundary(lim.last_receive_time)


async def handle_card_reward(client, pb: bytes):
    req = P.RechargeCardRewardC2S.FromString(pb)
    now = int(time.time())
    have = limits(client)
    taken, rewards = [], {}
    for group in dict.fromkeys(req.limit_group):
        lim = have.get(group)
        if lim is None or not claimable(lim, now):
            continue
        lim.last_receive_time = now
        pvp.state().setdefault("monthcard", {}).setdefault(str(client.gid), {})[str(group)] = now
        taken.append(lim)
        for item, n in REWARDS[group]:
            rewards[item] = rewards.get(item, 0) + n
    if taken:
        pvp.save()
        await client.send_packet(MSG_INFO, P.RechargeInfoS2C(limits=taken).SerializeToString())
        await pvp.HOOKS["grant_rewards"](client, list(rewards.items()), False)
        popup = P.NotifyRewardsS2C(origin=P.ORIGIN_CARD)
        for item, n in rewards.items():
            popup.items.add(conf_id=item, number=n)
        await client.send_packet(MSG_NOTIFY_REWARDS, popup.SerializeToString())
    await client.send_packet(MSG_CARD_REWARD, P.RechargeCardRewardS2C().SerializeToString())
    log.info(f"  CARD REWARD {list(req.limit_group)}: claimed {[l.limit_group for l in taken]} -> {rewards}")


def on_login(client) -> list[tuple[int, bytes]]:
    """Claims made here, on top of the baseline RechargeInfoS2C (merged by limit_group)."""
    mine = pvp.state().get("monthcard", {}).get(str(client.gid))
    if not mine:
        return []
    changed = [lim for g, lim in limits(client).items() if str(g) in mine]
    return [(MSG_INFO, P.RechargeInfoS2C(limits=changed).SerializeToString())] if changed else []


HANDLERS = {MSG_CARD_REWARD: handle_card_reward}
