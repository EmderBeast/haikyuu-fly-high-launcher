"""
Free packs of the shop: 0x2002 RechargeTestC2S{id = mallrecharge row, good_id, aid}.

Shown by the Garena captures (decrypted_3, messages 0155-0170: three claims, groups 4530, 2600, 1900):
  - the server sends RechargeInfoS2C{all = false, limits = [{limit_group, num = 1, start_time,
    last_reset_time}]}, the currency / item change, NotifyRewardsS2C with the row's ItemReward
    (100 x70000) and then the empty RechargeTestS2C
  - last_reset_time is the time of that first claim of the period, start_time 06:00 of that day
    (2026-11-16 06:00:00 UTC for claims at 22:18 UTC; another account's entries start on the Tuesday
    it claimed)
From the client's code (DateTimeUtils::CaculateNextRefreshTime, used for the pack's countdown):
  - LimitType 1 is renewed every day at 06:00, 2 every Monday at 06:00, 3 on the first of the month
    at 06:00; the client shows LimitNum minus RechargeLimitData.num as what is left

Before this the server only acknowledged the request: nothing was given.

Assumptions:
  - a pack that is used up for the period is refused with FAILED (never recorded)
  - 06:00 is meant in the game time zone the server names at login

Not served here (docs/garena-abgleich.md): rows that cost money, and free rows with another LimitType
(6: 54 rows) - what renews them is not in the client's countdown code.
"""
import calendar
import logging
import time

import matches
import monthcard
import pvp

P = matches.P
log = logging.getLogger("hfh")

MSG_INFO, MSG_PACK, MSG_NOTIFY_REWARDS = 0x2001, 0x2002, 0x0404
DAILY, WEEKLY, MONTHLY = 1, 2, 3
ROWS = {r["ID"]: r for r in pvp._cfg("mallrecharge")}


def _tz() -> int:
    return monthcard.TIME_ZONE_HOURS * 3600


def day_start(now: int) -> int:
    """The last 06:00 (game time zone) at or before now."""
    d = time.gmtime(now + _tz() - monthcard.RESET_HOUR * 3600)
    return calendar.timegm((d.tm_year, d.tm_mon, d.tm_mday, monthcard.RESET_HOUR, 0, 0)) - _tz()


def period_start(limit_type: int, now: int) -> int:
    """When the running period of that LimitType began."""
    start = day_start(now)
    d = time.gmtime(start + _tz())
    if limit_type == WEEKLY:
        return start - d.tm_wday * 86400  # Monday
    if limit_type == MONTHLY:
        return start - (d.tm_mday - 1) * 86400
    return start


def free_row(row_id: int) -> dict | None:
    row = ROWS.get(row_id)
    if not row or str(row.get("Price")) != "0" or row.get("LimitType") not in (DAILY, WEEKLY, MONTHLY):
        return None
    return row if monthcard.pairs(row.get("ItemReward")) and isinstance(row.get("LimitGroup"), int) else None


def entry(client, group: int) -> dict:
    """{"num", "start", "reset"} of that limit group: what was claimed here, else the baseline's."""
    mine = pvp.state().setdefault("freepack", {}).setdefault(str(client.gid), {})
    e = mine.get(str(group))
    if e is None:
        info = P.RechargeInfoS2C()
        try:
            info.ParseFromString(client.account.b.pb("0x2001"))
        except Exception:
            pass
        lim = next((x for x in info.limits if x.limit_group == group), None)
        e = mine[str(group)] = {"num": lim.num if lim else 0, "start": lim.start_time if lim else 0,
                                "reset": lim.last_reset_time if lim else 0}
    return e


def _limit(group: int, e: dict) -> "P.RechargeLimitData":
    return P.RechargeLimitData(limit_group=group, num=e["num"], start_time=e["start"], last_reset_time=e["reset"])


async def handle_pack(client, pb: bytes):
    req = P.RechargeTestC2S.FromString(pb)
    row = free_row(req.id)
    if row is None:  # a paid pack or a limit we cannot renew: as before
        log.info(f"  PACK {req.id}: not served")
        await client.send_packet(MSG_PACK, b"")
        return
    now = int(time.time())
    group = row["LimitGroup"]
    e = entry(client, group)
    if e["reset"] < period_start(row["LimitType"], now):  # a new period
        e.update(num=0, start=day_start(now), reset=now)
    if e["num"] >= (row.get("LimitNum") or 1):
        log.info(f"  PACK {req.id}: used up for this period")
        await client.send_packet(MSG_PACK, P.RechargeTestS2C(err=P.FAILED).SerializeToString())
        return
    e["num"] += 1
    pvp.save()
    rewards = monthcard.pairs(row["ItemReward"])
    await client.send_packet(MSG_INFO, P.RechargeInfoS2C(limits=[_limit(group, e)]).SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, False)
    popup = P.NotifyRewardsS2C()
    for item, n in rewards:
        popup.items.add(conf_id=item, number=n)
    await client.send_packet(MSG_NOTIFY_REWARDS, popup.SerializeToString())
    await client.send_packet(MSG_PACK, b"")
    log.info(f"  PACK {req.id} (group {group}): {rewards}, {e['num']}/{row.get('LimitNum') or 1}")


def on_login(client) -> list[tuple[int, bytes]]:
    """Packs claimed here, on top of the baseline RechargeInfoS2C (merged by limit_group)."""
    mine = pvp.state().get("freepack", {}).get(str(client.gid)) or {}
    lims = [_limit(int(g), e) for g, e in mine.items() if e["num"]]
    return [(MSG_INFO, P.RechargeInfoS2C(limits=lims).SerializeToString())] if lims else []


HANDLERS = {MSG_PACK: handle_pack}
