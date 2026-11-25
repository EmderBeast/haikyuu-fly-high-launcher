"""
Starter login set for new players (gids not listed in owners.json).

Built from the captured sync: personal state is rebuilt (cards, team, items,
currencies, profile, missions, sign-in, banner counters) or emptied (mail,
friends, club, chat, collections, progress ...); global game state (events,
shop config, notices, banner list, tutorial-done flags) stays as captured.
"""
import json
from pathlib import Path

import gameconfig
import wire
from accounts import CAPTURED, Baseline, _packed, make_card

OWNERS_FILE = Path(__file__).parent / "owners.json"

# personal systems that start empty for a new player
EMPTY = {
    "0x0C01",  # MailInfo
    "0x1001",  # FriendInfo
    "0x2E01", "0x2E11", "0x2E12", "0x2E1B", "0x2E1D",  # club (Association*)
    "0x1B01",  # ChatInfo
    "0x1D01",  # IllustratedInfo (gallery)
    "0x1C01",  # FavorInfo
    "0x2001",  # RechargeInfo
    "0x3001",  # HonorInfo (achievements)
    "0x0F07", "0x0F0A",  # lottery support / recall
    "0x1801",  # PlayerSpecialtyInfo (potentials)
    "0x2201",  # furniture
    "0x2A01",  # StageStarInfo
    "0x1101", "0x1501",  # training camp / practice match progress
    "0x5501",  # CoachInfo
    "0x0408",  # NotifyStatistics
    "0x3C01", "0x3C09",  # recalling
    "0x4001",  # TeamCompetitionInfo
    "0x2501",  # PlayerSupportInfo
    "0x1901",  # TreasureInfo
    "0x2701",  # FightTrainInfo
}

START_CURRENCY = {100: 200_000, 102: 50_000, 105: 180, 108: 500}  # gold, EXP, stamina, diamonds
START_ITEMS = {318: 10}  # recruit tickets
TEAM_SLOTS = 13  # 7 on court + bench
# PeakInfoS2C fields kept from the capture: everything except 3 (challenge list = other players' names).
# Login tests: dropping 2 (rank), 6/7 (formations) or 10/11 (scores) makes the client stall or disconnect.
PEAK_KEEP = {1, 2, 5, 6, 7, 8, 9, 10, 11}
INST_BASE = 1 << 60  # starter instance ids, far from captured ones


def owner_gids() -> set[int]:
    try:
        return set(json.loads(OWNERS_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return set()


def baseline_for(gid: int) -> Baseline:
    return CAPTURED if gid in owner_gids() else Baseline(starter_sync(gid))


def _starter_roster() -> list[int]:
    """R cards: the first 7 match the positions (Job) of the captured main line-up."""
    team = wire.parse(CAPTURED.pb("0x0B01"))
    lineup0 = wire.parse(next(v for n, _, v in team if n == 1))
    court = []
    for num, wt, v in lineup0:
        if num == 3:
            court += _packed(v) if wt == 2 else [v]
    jobs = [gameconfig.PLAYERS.get(wire.get(wire.parse(CAPTURED.cards.get(i, b"")), 3, 0), {}).get("Job")
            for i in court[:7]]
    # 5xxxx: copies of the real characters (opponent/special versions), not obtainable in game
    r_cards = [cid for cid, p in sorted(gameconfig.PLAYERS.items())
               if cid < 50000 and p.get("Rare") == 1 and gameconfig.new_card_fields(cid)
               and gameconfig.new_card_fields(cid)[1]]
    roster = []
    for job in jobs:
        pick = next((c for c in r_cards if gameconfig.PLAYERS[c].get("Job") == job and c not in roster), None)
        roster.append(pick or next(c for c in r_cards if c not in roster))
    roster += [c for c in r_cards if c not in roster][:TEAM_SLOTS - len(roster)]
    return roster


def _msg(msg_id: str, pb: bytes) -> dict:
    return {"msg_id": msg_id, "flags": 1, "body_hex": (b"\x00" + pb).hex()}


def starter_sync(gid: int) -> list[dict]:
    roster = _starter_roster()
    insts = [INST_BASE + i for i in range(len(roster))]
    cards = [make_card(inst, conf) for inst, conf in zip(insts, roster)]

    def team_pb():
        out = []
        for num, wt, v in wire.parse(CAPTURED.pb("0x0B01")):
            if num != 1:  # tactics, names, mode -> line-up choices stay
                out.append((num, wt, v))
                continue
            lineup = wire.get(wire.parse(v), 1, 0)
            f = [(1, 0, lineup)] if lineup else []
            f.append((3, 2, b"".join(wire.varint(i) for i in insts)))
            out.append((1, 2, wire.build(f)))
        return wire.build(out)

    def currency_pb():
        return b"".join(wire.build([(1, 2, wire.build([(1, 0, cid), (2, 0, START_CURRENCY.get(cid, 0))]))])
                        for cid in CAPTURED.currency)

    def items_pb():
        return b"".join(wire.build([(1, 2, wire.build([(1, 0, INST_BASE + 1000 + i), (2, 0, item), (3, 0, n)]))])
                        for i, (item, n) in enumerate(START_ITEMS.items()))

    def personal_pb():
        # PersonalInfoS2C: 2 sex, 3 name, 4 icon, 5 frame, 7 shown players (packed), 15 wallpaper
        keep = {2, 4, 5, 15, 17}
        f = [(n, wt, v) for n, wt, v in wire.parse(CAPTURED.pb("0x2101")) if n in keep]
        f += [(3, 2, f"Player{gid % 100000:05d}".encode()), (7, 2, b"".join(wire.varint(i) for i in insts[:3]))]
        return wire.build(sorted(f, key=lambda x: x[0]))

    def tasks_pb():
        # daily/weekly missions only, reset; activation counters zeroed (TaskActivation 1-3, level 4 = 1)
        out = []
        for tid, raw in CAPTURED.tasks.items():
            if (gameconfig.TASKS.get(tid, {}).get("TaskType") if gameconfig.AVAILABLE else None) in (1, 6):
                out.append((1, 2, wire.build([(1, 0, tid)])))
        act = [(n, wt, v) for n, wt, v in CAPTURED.activation_fields if n not in (1, 2, 3, 4, 5)]
        out.append((2, 2, wire.build(sorted(act + [(4, 0, 1)], key=lambda x: x[0]))))
        return wire.build(out)

    def lottery_pb():
        # banner list stays, counters/claims/shared history reset (LotteryUnit keep 1 id, 10 unlock, 12 wish list)
        out = []
        for raw in CAPTURED.lotteries.values():
            out.append((1, 2, wire.build([x for x in wire.parse(raw) if x[0] in (1, 10, 12)])))
        return wire.build(out)

    def peak_pb():
        # PeakInfoS2C must not be empty, and the client only finishes loading with complete defend/attack
        # formations (6/7: cards with computed stats and power) plus rank/score fields. Empty or stat-less
        # formations stall or disconnect it, so those stay as captured for now; only the challenge list
        # (other players' names) is dropped.
        return wire.build([x for x in wire.parse(CAPTURED.pb("0x4A01")) if x[0] in PEAK_KEEP])

    def home_pb():
        # HomeInfoS2C: the client reads base_info (1) unchecked; an empty message throws in its
        # dispatcher and the messages after it in that frame are lost (e.g. SpecificTrialInfo ->
        # Challenge chapter view crashes). Keep base/areas/schedules/shop, drop players (4 = cards),
        # visitors (5), messages (7), gamers (8), likes (9, 14).
        keep = {1, 2, 3, 6, 10, 11, 12, 13}
        return wire.build([x for x in wire.parse(CAPTURED.pb("0x2301")) if x[0] in keep])

    rebuilt = {
        "0x2301": home_pb(),
        "0x0901": wire.build([(1, 2, wire.build([(1, 2, c)])) for c in cards]),
        "0x0B01": team_pb(),
        "0x0407": currency_pb(),
        "0x0801": items_pb(),
        "0x2101": personal_pb(),
        "0x0E01": tasks_pb(),
        "0x0D01": wire.build([(1, 2, b"")]),
        "0x0F01": lottery_pb(),
        # PeakInfoS2C must not be empty (client stops loading); keep only the season (field 1),
        # drop rank, other players' challenge list and the captured teams
        "0x4A01": peak_pb(),
    }
    try:  # local debugging aid: message ids to keep as captured
        keep = set(json.loads((Path(__file__).parent / "starter_debug.json").read_text(encoding="utf-8")))
    except FileNotFoundError:
        keep = set()
    out = []
    for m in CAPTURED.sync:
        mid = m["msg_id"]
        if mid in keep:
            out.append(m)
        elif mid in rebuilt:
            out.append(_msg(mid, rebuilt[mid]))
        elif mid in EMPTY:
            out.append(_msg(mid, b""))
        else:
            out.append(m)
    return out
