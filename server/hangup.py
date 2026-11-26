"""
Extreme Training idle reward ("hang up", RingTrial): accrues per player, claimed with 0x1704. Before, every login
replayed the captured 0x1701 (its 8811 EXP / 2332 coins and its start time) and 0x1704 was answered from the
capture without giving anything.

Evidenced (decoded_tshark.json: one login, 26 pushes, two claims; contestrobot.json, contest.json):
  - contestrobot[floor].Award lists the rewards {8 item, 16 number, 24 period, 32 chance/10000}; floors 655-657:
    index 0 = item 102 x801, index 1 = item 127 x212, period 300 s. Every 300 s the server adds one Award to
    ContestDataHangUpReward{contest_id, index, number} and pushes 0x1702 with only that changed entry
    (2332 -> 2544 -> 2756; 8811 -> 9612; at login 11 periods since hang_up_start = 11 x 801 / 11 x 212);
  - the periods run on the server's clock, not from the claim: 49 s after a claim the next 212 arrived in full;
  - claim (0x1704, empty request): 0x1702 with ContestData.hang_up_start_time = now and every claimed entry with
    number 0, then 0x0405 with all currencies, the reward pop-up (index order: 102 before 127), then the empty
    0x1704 reply; entries already at 0 are not listed (second claim, 244 s after the first: only 102 x 801);
  - a period credits the floor of the previous period: after floor 657 was cleared, the next period still went
    to contest 655, the one after to 657 (both sets are kept and listed);
  - FightConTestInfoS2C at login: ContestData plus all non-zero entries, index order.
Assumed: the cap is contest constant 7 (300, 21600): at most 21600 s = 72 periods since hang_up_start (the
first-clear Reward of each floor is exactly 72 x Award); periods end on multiples of 300 s of unix time (Garena's
phase was ~135 s, index 0 ~15 s after index 1); a player starts accruing at the first login after this change,
with no stored rewards; a claim with nothing stored only restarts the time.
State: account "hangup" {"start", "last" (last period end), "floor" (floor credited next), "rewards" {"cid:idx": n}}.
"""
import asyncio
import logging
import random
import time

import pvp
import wire

log = logging.getLogger("hfh")

MSG_INFO, MSG_UPDATE, MSG_CLAIM = 0x1701, 0x1702, 0x1704
_CONST = {r["ID"]: r.get("Value") for r in pvp._cfg("contest")}
TICK, CAP = (list(_CONST.get(7) or [300, 21600]) + [300, 21600])[:2]
ROBOTS = {r["ID"]: r for r in pvp._cfg("contestrobot")}


def awards(floor: int) -> list[tuple[int, int, int, int]]:
    """contestrobot[floor].Award -> [(index, item, number, chance)]."""
    spec = (ROBOTS.get(floor) or {}).get("Award") or []
    flat = []
    for v in spec:
        flat.extend(v if isinstance(v, list) else [v])
    rows, row = [], {}
    for i in range(0, len(flat) - 1, 2):
        if flat[i] == 8 and row:
            rows.append(row)
            row = {}
        row[flat[i]] = flat[i + 1]
    if row:
        rows.append(row)
    return [(i, r.get(8, 0), r.get(16, 0), r.get(32, 10000)) for i, r in enumerate(rows) if r.get(8)]


def state(acc, now: float | None = None) -> dict:
    now = int(time.time() if now is None else now)
    if not acc.hangup:
        acc.hangup = {"start": now, "last": now, "floor": acc.contest_floor(), "rewards": {}}
    return acc.hangup


def settle(acc, now: float | None = None) -> list[str]:
    """Add every period that ended since the last one (up to the cap); returns the changed reward keys."""
    now = int(time.time() if now is None else now)
    st = state(acc, now)
    changed = []
    t = (max(st["last"], st["start"]) // TICK + 1) * TICK
    end = min(now, st["start"] + CAP)
    while t <= end:
        for index, item, number, chance in awards(st["floor"]):
            if chance >= 10000 or (chance > 0 and random.random() < chance / 10000):
                key = f"{st['floor']}:{index}"
                st["rewards"][key] = st["rewards"].get(key, 0) + number
                if key not in changed:
                    changed.append(key)
        st["floor"] = acc.contest_floor()  # the next period credits the floor reached by now
        t += TICK
    if now // TICK > st["last"] // TICK:  # a period ended (also past the cap): the floor reached so far counts next
        st["floor"] = acc.contest_floor()
    st["last"] = max(st["last"], now)
    return changed


def _entry(key: str, number: int) -> bytes:
    cid, index = (int(x) for x in key.split(":"))
    return wire.build([(n, 0, v) for n, v in ((1, cid), (2, index), (3, number)) if v])


def _sorted(keys):
    return sorted(keys, key=lambda k: tuple(int(x) for x in k.split(":")))


def info(acc, now: float | None = None) -> bytes:
    """FightConTestInfoS2C: ContestData with this player's start time and all stored rewards."""
    st = state(acc, now)
    settle(acc, now)
    acc.save()
    rewards = [(2, 2, _entry(k, st["rewards"][k])) for k in _sorted(st["rewards"]) if st["rewards"][k]]
    return wire.build([(1, 2, acc.contest_data())] + rewards)


def push_messages(acc, now: float | None = None) -> list[bytes]:
    """0x1702 bodies for the periods that ended: one per changed entry, highest index first (as captured)."""
    st = state(acc, now)
    changed = settle(acc, now)
    if changed:
        acc.save()
    order = sorted(changed, key=lambda k: (-int(k.split(":")[1]), int(k.split(":")[0])))
    return [wire.build([(2, 2, _entry(k, st["rewards"][k]))]) for k in order]


async def handle_claim(client, pb: bytes):
    acc = client.account
    now = int(time.time())
    st = state(acc, now)
    for body in push_messages(acc, now):  # periods that ended just now are part of the claim
        await client.send_packet(MSG_UPDATE, body)
    claimed = [k for k in _sorted(st["rewards"]) if st["rewards"][k]]
    totals: dict[int, int] = {}
    for key in claimed:
        cid, index = (int(x) for x in key.split(":"))
        aw = {i: item for i, item, _, _ in awards(cid)}
        item = aw.get(index)
        if item:
            totals[item] = totals.get(item, 0) + st["rewards"][key]
        st["rewards"][key] = 0
    st["start"] = now
    acc.save()
    await client.send_packet(MSG_UPDATE, wire.build([(1, 2, acc.contest_data())]
                                                    + [(2, 2, _entry(k, 0)) for k in claimed]))
    if totals:
        await pvp.HOOKS["grant_rewards"](client, list(totals.items()), True)
    log.info(f"  HANGUP claimed {totals}")
    await client.send_packet(MSG_CLAIM, b"")


async def loop():
    """Push the ended periods to everyone online, right after each period boundary."""
    import mail
    while True:
        await asyncio.sleep(TICK - time.time() % TICK + 1)
        for client in list(mail.ONLINE.values()):
            try:
                for body in push_messages(client.account):
                    await client.send_packet(MSG_UPDATE, body)
            except Exception as e:  # a closed connection must not stop the others
                log.error(f"  HANGUP push failed: {e!r}")


HANDLERS = {MSG_CLAIM: handle_claim}
