"""Memory draws (0x0F0B LotteryRecallDoC2S{id = recallgachaconf pool, count = cost value 1 / 10, is_free}).

Garena capture dizzy2711e (B1: pool 101, one single and one ten draw, tickets 9901 235 -> 234 -> 224):
  - messages per draw: PackItemChange (0x0802) of the tickets, LotteryRecallInfoS2C (0x0F0A, is_all false) with
    the pool's RecallLotteryUnit and the LotteryShare of its Cumulative group, RecallingUpdateS2C (0x3C02) with
    one new memory per draw (level 1, max_level 20, stage 1), then LotteryRecallDoS2C{rewards[{item{conf_id =
    the memory item 506001xxx, number 1}}]} (no is_decompose)
  - counters: unit.count and all_count + draws (0 -> 1 -> 11), up_ssr_count + draws (no top draw: 0 -> 1 -> 11),
    small_pool_cumulative 0 -> 1 -> 1 (the ten draw ended with two draws from SmallPool); share.false_count and
    all_count + draws (34 -> 35 -> 45, 615 -> 616 -> 626), got_card_infos = the last 200 draws {id, time}
  - the 11 draws: 8 from Pool (2-star memories 506001001-009, 048), 3 from SmallPool (3-star: 010, 039, 015)
Belegt from the tables / client (RecallGacha.*): group weights BigPool / SmallPool / Pool of recallgachaconf
(200 / 5170 / 94630 of 100000 for pool 101), the drop inside a group by recallgachapoolconf.Drop weight
(AddDrop entries after their date), the top guarantee at BigFloors counted by share.false_count, soft pity from
StartAddTimes by AddProbability (out of 100000) per draw (rule text "ShowStartAddTimes / ShowAddProbability").
Annahmen:
  - small_pool_cumulative = 1 after a SmallPool / BigPool draw, else + 1; SmallFloors (10) guarantees a
    SmallPool draw when it reaches SmallFloors; false_count and up_ssr_count go to 0 on a BigPool draw
  - a wish (recallwish.py) does not change the draw (pool 101 takes none; no capture of a wished pool)
  - no auto-dismantle of new memories (recalling.py auto list), no free draw
"""
import json
import logging
import random
import time

import gameconfig
import matches
import pvp
import recalling as R
import recallwish
import wire

P = matches.P
log = logging.getLogger("hfh")

MSG_DO = 0x0F0B
SCALE = 100000
POOLS = {r["ID"]: r for r in pvp._cfg("recallgachaconf")}
DROPS = {r["ID"]: r for r in pvp._cfg("recallgachapoolconf")}
BY_ITEM = {r.get("ItemID"): r["ID"] for r in R.RECALLING.values() if r.get("ItemID")}
HISTORY = 200


def _group(row, key) -> tuple[int, int]:
    p = R.pairs(row.get(key))
    return p[0] if p else (0, 0)


def drops(pool_id: int, now: float | None = None) -> list[tuple[int, int]]:
    """(item, weight) of a drop pool: Drop entries [8 item, 16 number, 24 weight] and AddDrop
    "item_num_weight_date_time" after their date."""
    row = DROPS.get(pool_id, {})
    flat, out, cur = R._flat(row.get("Drop")), [], None
    for tag, val in zip(flat[::2], flat[1::2]):
        if tag == 8:
            cur = [val, 1]
            out.append(cur)
        elif tag == 24 and cur is not None:
            cur[1] = val
    now = now or time.time()
    for e in row.get("AddDrop") or []:
        parts = str(e).split("_")
        try:
            when = time.mktime(time.strptime("_".join(parts[3:5]), "%Y-%m-%d_%H:%M:%S"))
            if now >= when:
                out.append([int(parts[0]), int(parts[2])])
        except (ValueError, IndexError):
            continue
    return [(i, w) for i, w in out if w > 0]


def _state(gid: int) -> dict:
    return pvp.state().setdefault("recall_draws", {}).setdefault(str(gid), {"units": {}, "shares": {}})


def unit_of(gid: int, acc, pool: int):
    s = _state(gid)["units"].get(str(pool))
    if s:
        u = P.RecallLotteryUnit.FromString(bytes.fromhex(s))
        wish = recallwish.units(gid, acc).get(pool)
        u.wish = wish.wish if wish is not None else u.wish
        return u
    return recallwish.units(gid, acc).get(pool) or P.RecallLotteryUnit(pool_id=pool)


def share_of(gid: int, acc, group: int):
    s = _state(gid)["shares"].get(str(group))
    if s:
        return P.LotteryShare.FromString(bytes.fromhex(s))
    try:
        base = P.LotteryRecallInfoS2C.FromString(acc.b.pb("0x0F0A"))
        found = next((x for x in base.share_data if x.cumulative_group_id == group), None)
    except Exception:
        found = None
    out = P.LotteryShare(cumulative_group_id=group)
    if found is not None:
        out.CopyFrom(found)
    return out


def draw(row: dict, unit, share, n: int, rng=random) -> list[int]:
    """n memory items; unit / share counters advanced."""
    big, small, pool = _group(row, "BigPool"), _group(row, "SmallPool"), _group(row, "Pool")
    start, add = row.get("StartAddTimes") or 0, row.get("AddProbability") or 0
    out = []
    for _ in range(n):
        pulls = share.false_count + 1
        p_big = big[1] + (max(0, pulls - start + 1) * add if start and pulls >= start else 0)
        if row.get("BigFloors") and pulls >= row["BigFloors"]:
            group = big[0]
        else:
            roll = rng.random() * SCALE
            group = big[0] if roll < p_big else (small[0] if roll < p_big + small[1] else pool[0])
            if group == pool[0] and row.get("SmallFloors") and unit.small_pool_cumulative >= row["SmallFloors"]:
                group = small[0]
        items = drops(group)
        item = rng.choices([i for i, _ in items], weights=[w for _, w in items])[0] if items else 0
        out.append(item)
        unit.count += 1
        unit.all_count += 1
        share.all_count += 1
        if group == big[0]:
            share.false_count, unit.up_ssr_count = 0, 0
        else:
            share.false_count += 1
            unit.up_ssr_count += 1
        unit.small_pool_cumulative = 1 if group in (big[0], small[0]) else unit.small_pool_cumulative + 1
        share.got_card_infos.add(id=item, time=int(time.time()))
    if len(share.got_card_infos) > HISTORY:
        keep = list(share.got_card_infos)[-HISTORY:]
        del share.got_card_infos[:]
        share.got_card_infos.extend(keep)
    return out


async def handle_do(client, pb: bytes, rng=random):
    from accounts import MSG_ITEM_CHANGE
    acc, gid = client.account, int(client.gid)
    req = P.LotteryRecallDoC2S.FromString(pb)
    row = POOLS.get(req.id)
    fail = P.LotteryRecallDoS2C(err=P.FAILED).SerializeToString()
    if row is None:
        return await client.send_packet(MSG_DO, fail)
    one, ten = (R.pairs(row.get("OneCost")) or [(0, 0)])[0], (R.pairs(row.get("TenCost")) or [(0, 0)])[0]
    n, cost = (10, ten) if req.count == ten[1] and ten[1] else (1, one)
    unit = unit_of(gid, acc, req.id)
    if not cost[0] or acc.item_count(cost[0]) < cost[1] or unit.count + n > (row.get("Limit") or 9999):
        log.info(f"  RECALL DRAW {req.id} x{n} refused")
        return await client.send_packet(MSG_DO, fail)
    group = row.get("Cumulative") or 0
    share = share_of(gid, acc, group)
    items = draw(row, unit, share, n, rng)
    st = _state(gid)
    st["units"][str(req.id)] = unit.SerializeToString().hex()
    st["shares"][str(group)] = share.SerializeToString().hex()
    pvp.save()
    await client.send_packet(MSG_ITEM_CHANGE, wire.build([(1, 2, acc.set_item(cost[0], acc.item_count(cost[0]) - cost[1]))]))
    await client.send_packet(recallwish.MSG_INFO, P.LotteryRecallInfoS2C(lotterys=[unit], share_data=[share]).SerializeToString())
    created = [P.PlayerRecalling.FromString(acc.add_recalling(BY_ITEM[i])) for i in items if i in BY_ITEM]
    acc.save()
    upd = P.RecallingUpdateS2C()
    for r in created:
        upd.recalling_creates.add().CopyFrom(r)
    await client.send_packet(R.MSG_UPDATE, upd.SerializeToString())
    rep = P.LotteryRecallDoS2C()
    for i in items:
        rep.rewards.add().item.CopyFrom(P.Item(conf_id=i, number=1))
    await client.send_packet(MSG_DO, rep.SerializeToString())
    log.info(f"  RECALL DRAW pool {req.id} x{n}: {items}")


def info(gid: int, acc) -> bytes:
    """0x0F0A at login: recallwish's units with this player's draws and shares."""
    m = P.LotteryRecallInfoS2C.FromString(recallwish.info(gid, acc))
    st = _state(gid)
    for u in m.lotterys:
        if str(u.pool_id) in st["units"]:
            u.CopyFrom(unit_of(gid, acc, u.pool_id))
    have = {u.pool_id for u in m.lotterys}
    m.lotterys.extend(unit_of(gid, acc, int(p)) for p in st["units"] if int(p) not in have)
    for s in m.share_data:
        if str(s.cumulative_group_id) in st["shares"]:
            s.CopyFrom(share_of(gid, acc, s.cumulative_group_id))
    have = {s.cumulative_group_id for s in m.share_data}
    m.share_data.extend(share_of(gid, acc, int(g)) for g in st["shares"] if int(g) not in have)
    return m.SerializeToString()


HANDLERS = {MSG_DO: handle_do}
