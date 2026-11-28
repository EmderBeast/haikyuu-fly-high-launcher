"""
Starter login set for new players (gids not listed in owners.json).

Garena's own login of a brand-new guest account (data/fresh_sync.json, capture neu2811 of 28.11.2026):
every personal message as Garena sent it (currencies, items, profile unlocks, tasks, home, activities ...),
with this player's id, card / item ids and the times of the account's creation in place of Garena's. Global
game state (events, shop config, notices, banners) stays from the captured sync, the recruit banners too
(Garena unlocks them during the tutorial).
"""
import json
from pathlib import Path

import gameconfig
import wire
from accounts import CAPTURED, Baseline, _packed, make_card

OWNERS_FILE = Path(__file__).parent / "owners.json"

INST_BASE = 1 << 60  # starter instance ids, far from captured ones


def default_look() -> dict[int, int]:
    """PersonalInfoS2C field -> item a new player wears: 4 icon, 5 frame (playerinfoitemconfig: IsDefalt
    and Iswear; ItemType 1 = icon, 2 = frame). Falls back to the captured ones without game data."""
    try:
        rows = json.loads((gameconfig.CFG / "playerinfoitemconfig.json").read_text(encoding="utf-8"))
    except Exception:
        rows = []
    out = {}
    for field, kind in ((4, 1), (5, 2)):
        worn = sorted((r.get("Priority", 0), r["ID"]) for r in rows
                      if r.get("ItemType") == kind and r.get("IsDefalt") == 1 and r.get("Iswear") == 1)
        if worn:
            out[field] = worn[0][1]
        else:
            out[field] = wire.get(wire.parse(CAPTURED.pb("0x2101")), field, 0)
    return out


def owner_gids() -> set[int]:
    try:
        return set(json.loads(OWNERS_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return set()


def baseline_for(gid: int) -> Baseline:
    return CAPTURED if gid in owner_gids() else Baseline(starter_sync(gid))


# Garena's start for a new account (Emre's new guest account, 28.11.2026, line-up screenshots of
# stages 1-1 and 1-2): Hinata's middle school Yukigaoka, all level 1, no libero, in this line-up order
# (place 1-6): Izumi S, Mori WS, Hinata MB, Suzuki OP, Sekimukai WS, Kawashima MB. The story's lent
# players stand in for the first one with their position (1-1 Sugawara for Izumi, 1-2 Tanaka for Mori).
START_ROSTER = [3106, 3104, 3101, 3102, 3105, 3103]


def _starter_roster() -> list[int]:
    return [c for c in START_ROSTER if not gameconfig.AVAILABLE or c in gameconfig.PLAYERS]


def _msg(msg_id: str, pb: bytes) -> dict:
    return {"msg_id": msg_id, "flags": 1, "body_hex": (b"\x00" + pb).hex()}


FRESH_FILE = Path(__file__).parent / "data" / "fresh_sync.json"
MSG_IDS = Path(__file__).parent.parent / "decoded" / "message_ids.json"
FRESH_EMPTY = {"0x0C01"}  # Garena's start mails were hotfix compensation of that week, not a new-player gift
DAY = 86400


def _fresh() -> dict:
    return json.loads(FRESH_FILE.read_text(encoding="utf-8"))


def created(gid: int) -> int:
    """When this new player first logged in (Garena's registration time in the fresh messages is moved there)."""
    import accounts
    path = accounts.ACCOUNTS / "starter_created.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        d = {}
    if str(gid) not in d:
        import time
        d[str(gid)] = int(time.time())
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(d), encoding="utf-8")
    return d[str(gid)]


def _types() -> dict[str, str]:
    try:
        return {e["msg_id"].upper(): (e.get("s2c_type") or "").split(".")[-1]
                for e in json.loads(MSG_IDS.read_text(encoding="utf-8"))}
    except FileNotFoundError:
        return {}


def _remap(msg, ids: dict[int, int], t0: int, t: int) -> None:
    """Garena's ids -> ours; times of the registration (and whole days after it) -> this player's."""
    for f, v in msg.ListFields():
        rep = getattr(f, "is_repeated", None)
        rep = f.label == f.LABEL_REPEATED if rep is None else rep
        if f.type == f.TYPE_MESSAGE:
            if f.message_type.GetOptions().map_entry:
                continue
            for x in (v if rep else [v]):
                _remap(x, ids, t0, t)
            continue
        if f.cpp_type not in (f.CPPTYPE_INT64, f.CPPTYPE_UINT64, f.CPPTYPE_INT32, f.CPPTYPE_UINT32):
            continue

        def conv(x):
            if x in ids:
                return ids[x]
            d = x - t0
            if abs(d) <= 600 or (DAY - 600 <= d <= 90 * DAY and min(d % DAY, DAY - d % DAY) <= 600):
                return x + t - t0
            return x
        if rep:
            for i, x in enumerate(v):
                v[i] = conv(x)
        else:
            setattr(msg, f.name, conv(v))


def starter_sync(gid: int) -> list[dict]:
    from matches import P
    fresh = _fresh()
    t0, t = fresh["t0"], created(gid)
    types = _types()
    roster = _starter_roster()
    insts = [INST_BASE + i for i in range(len(roster))]
    cards = [make_card(inst, conf) for inst, conf in zip(insts, roster)]
    ids = {fresh["gid"]: gid}
    msgs = {m["msg_id"].upper(): m for m in fresh["msgs"]}
    pack = P.PlayerCardPackS2C.FromString(bytes.fromhex(msgs["0X0901"]["body_hex"])[1:])
    free = dict(zip(roster, insts))
    for p in pack.players:  # Garena's card ids -> ours, by character
        if p.info.conf_id in free:
            ids[p.info.inst_id] = free.pop(p.info.conf_id)
    for i, it in enumerate(P.PackItemInfoS2C.FromString(bytes.fromhex(msgs["0X0801"]["body_hex"])[1:]).items):
        ids[it.inst_id] = INST_BASE + 1000 + i

    def lottery_pb():
        # banner list stays, counters/claims/shared history reset (LotteryUnit keep 1 id, 10 unlock, 12 wish list)
        out = []
        for raw in CAPTURED.lotteries.values():
            out.append((1, 2, wire.build([x for x in wire.parse(raw) if x[0] in (1, 10, 12)])))
        return wire.build(out)

    rebuilt = {
        "0X0901": wire.build([(1, 2, wire.build([(1, 2, c)])) for c in cards]),
        "0X0F01": lottery_pb(),
    }
    try:  # local debugging aid: message ids to keep as captured
        keep = {k.upper() for k in json.loads((Path(__file__).parent / "starter_debug.json").read_text(encoding="utf-8"))}
    except FileNotFoundError:
        keep = set()
    captured = {m["msg_id"].upper(): m for m in CAPTURED.sync}
    out = []
    for m in fresh["msgs"]:
        mid = m["msg_id"].upper()
        if mid in keep or m.get("from") == "captured":
            if mid in captured:
                out.append(captured[mid])
            continue
        if mid in rebuilt:
            out.append(_msg(m["msg_id"], rebuilt[mid]))
            continue
        if mid in {x.upper() for x in FRESH_EMPTY}:
            out.append(_msg(m["msg_id"], b""))
            continue
        pb = bytes.fromhex(m["body_hex"])[1:]
        T = getattr(P, types.get(mid, ""), None)
        if T is not None and pb:
            msg = T.FromString(pb)
            _remap(msg, ids, t0, t)
            if mid == "0X2101":  # profile: our name for new players, the game's default icon and frame
                msg.name = f"Player{gid % 100000:05d}"
                look = default_look()
                msg.icon_id, msg.icon_frame_id = look.get(4, msg.icon_id), look.get(5, msg.icon_frame_id)
            if mid == "0X2801":
                # local activities Garena opens later (online rewards, lucky draw, stamina, training supply):
                # our modules fill them per player, so they come along from the start
                have = {a.type for a in msg.activity_list}
                for a in P.LocalActivityInfoS2C.FromString(CAPTURED.pb("0x2801")).activity_list:
                    if a.type not in have:
                        msg.activity_list.add().CopyFrom(a)
            pb = msg.SerializeToString()
        out.append(_msg(m["msg_id"], pb))
    return out
