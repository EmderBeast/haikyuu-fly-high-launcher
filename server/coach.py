"""
Coaches per player: level up (0x5503) and dismantle (0x5505). Before, both were answered from the capture and a
player's coaches never changed; every account's login sent the baseline's coach list.

Evidenced (decoded_tshark.json: 14 level-ups of coach conf 18 from level 1 to 15, then its dismantling; tables
coachlevel, coachconstant, coachlist):
  - CoachUpLevelC2S{inst_id, consume_count}: consume_count of currency coachconstant 1 (336) is taken; it was
    exactly coachlevel[rarity][level].Experience every time (3000, 5000, ... 29000 for rarity 4), the coach
    rose one level with exp 0; maximum = the table's last level (15);
  - at the levels of coachconstant 2 (3, 6, 9, 12, 15) the coach gets one more job buff {job, buff_id}
    (captured: L3 1/4035, L6 5/1014, L9 5/2056, L12 3/1014, L15 1/2053);
  - order: 0x0405 currency, 0x5502 CoachUpdate with the coach, (0x0E02 when an achievement moved), 0x5503 reply;
  - CoachDecompC2S{inst_ids}: 0x5502 with del_coach_card_inst_ids, then currency 336 += coachlevel[rarity]
    [level].Resolve (level 15: 180800, 164990 -> 345790), reward pop-up, 0x0E02, 0x5505 reply;
  - achievements (TaskType 8): 91202 [L] "Coach Upgrade" +1 when a coach reaches level L (8401507 [15] went to
    205 at the level-15 step), 91204 "Coach Dismantling" moved at the dismantling.
Assumed: the job is drawn with coachconstant 3 (all 50), the buff from the coach's CoachBuffId list weighted by
coachbuff Weight (five samples only); a consume_count above one level's cost raises several levels and keeps the
rest as exp; 91204 counts one per coach; locked coaches cannot be dismantled.
Not built: lock (0x5504), settings (0x5506); matches still use the captured room's coach card.
State: pvp.json "coaches" {gid: {"cards": {inst: CoachCard hex}, "deleted": [inst]}}.
"""
import random

import pvp
import wire

MSG_INFO, MSG_UPDATE, MSG_UP, MSG_DECOMP = 0x5501, 0x5502, 0x5503, 0x5505
CONST = {r["ID"]: r for r in pvp._cfg("coachconstant")}
CURRENCY = CONST.get(1, {}).get("Data1", 336)
BUFF_LEVELS = [int(x) for x in str(CONST.get(2, {}).get("Data2") or "3,6,9,12,15").split(",") if x.strip()]
JOB_WEIGHTS = [tuple(int(v) for v in p.split("_")) for p in str(CONST.get(3, {}).get("Data2") or "").split(",") if "_" in p]
LEVELS = {(r["CoachRare"], r["Level"]): r for r in pvp._cfg("coachlevel")}
MAX_LEVEL = {}
for (rare, lv) in LEVELS:
    MAX_LEVEL[rare] = max(MAX_LEVEL.get(rare, 0), lv)
COACHES = {r["ID"]: r for r in pvp._cfg("coachlist")}
BUFFS = {r["ID"]: r for r in pvp._cfg("coachbuff")}
UPGRADE_ACH, DISMANTLE_ACH = 91202, 91204


def _P():
    import matches
    return matches.P


def _store(gid: int) -> dict:
    return pvp.state().setdefault("coaches", {}).setdefault(str(gid), {"cards": {}, "deleted": []})


def cards(gid: int, acc) -> dict:
    """inst_id -> CoachCard: the baseline's coaches with this player's changes."""
    P = _P()
    out = {}
    try:
        for c in P.CoachInfoS2C.FromString(acc.b.pb("0x5501")).coach_cards:
            out[c.inst_id] = c
    except Exception:
        pass
    st = _store(gid)
    for inst in st["deleted"]:
        out.pop(int(inst), None)
    for inst, raw in st["cards"].items():
        out[int(inst)] = P.CoachCard.FromString(bytes.fromhex(raw))
    return out


def info(gid: int, acc) -> bytes:
    P = _P()
    out = P.CoachInfoS2C()
    try:
        out.CopyFrom(P.CoachInfoS2C.FromString(acc.b.pb("0x5501")))
    except Exception:
        pass
    del out.coach_cards[:]
    out.coach_cards.extend(sorted(cards(gid, acc).values(), key=lambda c: c.inst_id))
    return out.SerializeToString()


def _save(gid: int, card):
    _store(gid)["cards"][str(card.inst_id)] = card.SerializeToString().hex()
    pvp.save()


def _rare(card) -> int:
    return COACHES.get(card.conf_id, {}).get("CoachRare", 2)


def _new_buff(card, rng=random):
    jobs = JOB_WEIGHTS or [(j, 1) for j in range(1, 6)]
    job = rng.choices([j for j, _ in jobs], weights=[w for _, w in jobs])[0]
    pool = [b for b in COACHES.get(card.conf_id, {}).get("CoachBuffId") or [] if b in BUFFS]
    if not pool:
        return None
    buff = rng.choices(pool, weights=[max(1, BUFFS[b].get("Weight") or 1) for b in pool])[0]
    return job, buff


def level_up(card, consume: int, rng=random) -> list[int]:
    """Raise the coach with `consume` currency; returns the levels reached."""
    rare, reached = _rare(card), []
    exp = card.exp + consume
    while card.level < MAX_LEVEL.get(rare, 15):
        cost = LEVELS.get((rare, card.level), {}).get("Experience") or 0
        if cost <= 0 or exp < cost:
            break
        exp -= cost
        card.level += 1
        reached.append(card.level)
        if card.level in BUFF_LEVELS:
            nb = _new_buff(card, rng)
            if nb:
                card.job_buffs.add(job=nb[0], buff_id=nb[1])
    card.exp = exp if card.level < MAX_LEVEL.get(rare, 15) else 0
    return reached


def _achievements(client, code: int, amount: int = 1, level: int | None = None):
    """Coach achievements (TaskType 8): 91202 [L] when a coach reaches level L, 91204 per dismantled coach."""
    import gameconfig
    from accounts import TASK_FINISH
    m = getattr(client, "missions", None)
    if m is None:
        return
    for tid in m.acc.all_task_ids():
        cfg = gameconfig.TASKS.get(tid, {})
        if cfg.get("TaskSubTargetType") != code or m.acc.task_state(tid) != 0:
            continue
        if code == UPGRADE_ACH and cfg.get("TastConditionTow") != level:
            continue
        progress = wire.get(wire.parse(m.acc.task(tid)), 3, 0) + amount
        target = cfg.get("Value") or 1
        m._set(tid, TASK_FINISH if progress >= target else 0, min(progress, target))


async def _flush_missions(client):
    m = getattr(client, "missions", None)
    if m is not None and m.changed_tasks:
        m.save()
        client.account.save()
        await client.send_packet(0x0E02, m.change_message())


async def handle_up(client, pb: bytes):
    P = _P()
    gid, acc = int(client.gid), client.account
    req = P.CoachUpLevelC2S.FromString(pb)
    card = cards(gid, acc).get(req.inst_id)
    rare = _rare(card) if card else 0
    cost = LEVELS.get((rare, card.level), {}).get("Experience", 0) if card else 0
    if (card is None or card.level >= MAX_LEVEL.get(rare, 15) or req.consume_count < cost or req.consume_count <= 0
            or acc.get_currency(CURRENCY) < req.consume_count):
        await client.send_packet(MSG_UP, P.CoachUpLevelS2C(err=1).SerializeToString())
        return
    entry = acc.set_currency(CURRENCY, acc.get_currency(CURRENCY) - req.consume_count)
    acc.save()
    await client.send_packet(0x0405, wire.build([(1, 2, entry)]))
    reached = level_up(card, req.consume_count)
    _save(gid, card)
    await client.send_packet(MSG_UPDATE, P.CoachUpdateS2C(coach_cards=[card]).SerializeToString())
    for lv in reached:
        _achievements(client, UPGRADE_ACH, 1, lv)
    await _flush_missions(client)
    await client.send_packet(MSG_UP, P.CoachUpLevelS2C().SerializeToString())


async def handle_decomp(client, pb: bytes):
    P = _P()
    gid, acc = int(client.gid), client.account
    ids = list(P.CoachDecompC2S.FromString(pb).inst_ids)
    have = cards(gid, acc)
    picked = [have[i] for i in ids if i in have and not have[i].locked]
    if not ids or len(picked) != len(set(ids)):
        await client.send_packet(MSG_DECOMP, P.CoachDecompS2C(err=1).SerializeToString())
        return
    refund = sum(LEVELS.get((_rare(c), c.level), {}).get("Resolve") or 0 for c in picked)
    st = _store(gid)
    for c in picked:
        st["cards"].pop(str(c.inst_id), None)
        if c.inst_id not in st["deleted"]:
            st["deleted"].append(c.inst_id)
    pvp.save()
    await client.send_packet(MSG_UPDATE, P.CoachUpdateS2C(del_coach_card_inst_ids=[c.inst_id for c in picked])
                             .SerializeToString())
    if refund:
        await pvp.HOOKS["grant_rewards"](client, [(CURRENCY, refund)], True)
    _achievements(client, DISMANTLE_ACH, len(picked))
    await _flush_missions(client)
    await client.send_packet(MSG_DECOMP, P.CoachDecompS2C().SerializeToString())


HANDLERS = {MSG_UP: handle_up, MSG_DECOMP: handle_decomp}
