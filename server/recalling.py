"""Memories (PlayerRecalling) per account: level up and breakthrough, stage up, dismantle, auto-dismantle list.

  0x3C04 RecallingUpgradeLevelC2S{inst_id, consume_inst_ids, items[Item{inst_id, number}]}
         (only inst_id when level == max_level: breakthrough, RecallingLevelUpView::OnBtnBreakThroughEvent)
  0x3C05 RecallingUpgradeStageC2S{inst_id, consume_inst_ids, consume_items_inst_ids (one entry per unit)}
  0x3C08 RecallingDeComposeC2S{inst_ids}
  0x3C0A RecallingAutoResolveC2S{ids (conf ids on), unids (off)} -> S2C{setting = the whole list}
  changes: 0x3C02 RecallingUpdateS2C{1 creates, 2 updates, 3 removes}

No capture of these requests exists. Belegt from the client code (decoded/analysis/hotfix_il_full.txt) and
the tables:
  - level-up exp: exp items recallingconstant 2 (507001001 = 100, ...002 = 1000, ...003 = 10000) x number,
    each consumed memory recallinglevelup.NumExp at its star and level (RecallingConfigUtils::GetExpOfItem /
    GetExpOfRecalling); level by level recallinglevelup.Exp (ID star*1000 + level) minus the exp already held,
    up to max_level (RecallingLevelUpgradeModel::TryUpgrade / ToAddLevel)
  - gold (100) charged: recallingstar.NumGold per consumed memory + recallingconstant 11 per exp item
    (RecallingLevelUpView::GetGoldConsumption, the value the client checks before sending)
  - breakthrough: cost recallingarrt (Group = recalling.AttrGroupId, Level = max_level).UpgradeConsume
    (pairs 8 = item, 16 = number; 100 = gold); new max_level = next recallingarrt Level above it, at most
    recallingconstant 3 (80) - chain 20 -> 40 -> 60 -> 70 -> 80. New memories have max_level 20 (362 captured
    level-1 memories, all with max_level 20; captured 20/40 and 40/60 pairs show the steps)
  - stage: +1 per unit (a copy of the same memory or the stage item of recallingconstant 6 for its star),
    at most recallingconstant 1 (5); gold = recallingskill.Gold of rows SkillId*10 + s for each stage s passed
  - dismantle: only unequipped, unlocked, level <= 1, stage <= 1, star 2 or 3 (constant 10236);
    gives recallingconstant 12 (507001001) x (sum of NumExp) // recallingconstant 13 (100)
  - auto-dismantle: ids switched on / off by conf id, the reply carries the whole list (RecallingInfoS2C.setting
    at login is the same list)
Annahmen:
  - exp beyond max_level is lost (the client's preview stops there; RecallingUpgradeLevelS2C.rewards may be
    Garena's refund of it - not recorded)
  - equipped or locked memories are not consumed; missing gold/items: FAILED; order: costs, 0x3C02, reply
  - what Garena itself dismantles with the auto list (new draws) is not built: the list is stored and sent
Not built (needs a capture): 0x0F0B memory draws (pools, pity, what a wish changes).
"""
import json
import logging

import gameconfig
import matches
import wire

P = matches.P
log = logging.getLogger("hfh")

MSG_UPDATE, MSG_LEVEL, MSG_STAGE, MSG_DECOMP, MSG_AUTO = 0x3C02, 0x3C04, 0x3C05, 0x3C08, 0x3C0A
GOLD = 100
DECOMP_STARS = (2, 3)  # constant.json 10236 "2,3"


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


def _flat(v):
    out = []
    for x in v if isinstance(v, list) else ([] if v is None else [v]):
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def pairs(v) -> list[tuple[int, int]]:
    """Table lists of 8 = id, 16 = number: [(id, number)] (a lone 8 = id gives number 0)."""
    out = []
    flat = _flat(v)
    for key, val in zip(flat[::2], flat[1::2]):
        if key == 8:
            out.append([val, 0])
        elif key == 16 and out:
            out[-1][1] = val
    return [tuple(x) for x in out]


RECALLING = {r["ID"]: r for r in _load("recalling")}
LEVELUP = {(r["Star"], r["Level"]): r for r in _load("recallinglevelup")}
ARRT: dict[int, list] = {}
for _r in _load("recallingarrt"):
    ARRT.setdefault(_r["Group"], []).append(_r)
for _v in ARRT.values():
    _v.sort(key=lambda r: r.get("Level", 0))
STAR_GOLD = {r["ID"]: r.get("NumGold") or 0 for r in _load("recallingstar")}
SKILL = {r["ID"]: r for r in _load("recallingskill")}
CONST = {r["ID"]: r.get("Value") for r in _load("recallingconstant")}
MAX_STAGE = dict(pairs(CONST.get(1)))
EXP_ITEM = dict(pairs(CONST.get(2)))
MAX_LEVEL = dict(pairs(CONST.get(3)))
STAGE_ITEM = dict(pairs(CONST.get(6)))
ITEM_GOLD = dict(pairs(CONST.get(11)))
DECOMP_ITEM = (pairs(CONST.get(12)) or [(507001001, 0)])[0][0]
DECOMP_DIV = (pairs(CONST.get(13)) or [(100, 0)])[0][0] or 100


def star(rec) -> int:
    return RECALLING.get(rec.conf_id, {}).get("Star", 0)


def level_up(rec, exp_in: int) -> int:
    """Adds exp up to max_level; returns the exp that did not fit."""
    first = True
    while exp_in > 0 and rec.level < rec.max_level:
        row = LEVELUP.get((star(rec), rec.level))
        if not row or not row.get("Exp"):
            break
        need = row["Exp"] - (rec.exp if first else 0)
        first = False
        if exp_in < need:
            rec.exp += exp_in
            return 0
        exp_in -= need
        rec.level += 1
        rec.exp = 0
    if rec.level >= rec.max_level:
        rec.exp = 0
    return exp_in


def breakthrough(rec) -> tuple[int, list[tuple[int, int]]] | None:
    """(new max_level, cost) when the memory stands at its cap, else None."""
    top = MAX_LEVEL.get(star(rec), 0)
    if rec.level != rec.max_level or rec.max_level >= top:
        return None
    rows = ARRT.get(RECALLING.get(rec.conf_id, {}).get("AttrGroupId"), [])
    here = next((r for r in rows if r.get("Level", 0) == rec.max_level), None)
    nxt = next((r.get("Level", 0) for r in rows if r.get("Level", 0) > rec.max_level), top)
    if here is None:
        return None
    return min(nxt, top), [(i, n) for i, n in pairs(here.get("UpgradeConsume")) if n > 0]


def stage_gold(rec, new_stage: int) -> int:
    skill = RECALLING.get(rec.conf_id, {}).get("SkillId", 0)
    return sum(SKILL.get(skill * 10 + s, {}).get("Gold") or 0 for s in range(rec.stage, new_stage))


def decomp_reward(recs) -> int:
    return sum(LEVELUP.get((star(r), max(r.level, 1)), {}).get("NumExp") or 0 for r in recs) // DECOMP_DIV


# ------------------------------------------------------------------ account access
def get(acc, inst: int):
    raw = acc.all_recallings().get(inst)
    return P.PlayerRecalling.FromString(raw) if raw else None


def _update(creates=(), updates=(), removes=()) -> bytes:
    m = P.RecallingUpdateS2C()
    for r in creates:
        m.recalling_creates.add().CopyFrom(r)
    for r in updates:
        m.recalling_updates.add().CopyFrom(r)
    m.recalling_removes.extend(removes)
    return m.SerializeToString()


async def _pay(client, costs: dict[int, int]) -> bool:
    """Takes currencies / bag items (absolute counts checked first); sends the changes. False = not enough."""
    from accounts import MSG_CURRENCY_CHANGE, MSG_ITEM_CHANGE
    acc = client.account
    have = {i: (acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i)) for i in costs}
    if any(have[i] < n for i, n in costs.items()):
        return False
    cur, items = [], []
    for i, n in costs.items():
        if not n:
            continue
        if acc.is_currency(i):
            cur.append((1, 2, acc.set_currency(i, have[i] - n)))
        else:
            items.append((1, 2, acc.set_item(i, have[i] - n)))
    if items:
        await client.send_packet(MSG_ITEM_CHANGE, wire.build(items))
    if cur:
        await client.send_packet(MSG_CURRENCY_CHANGE, wire.build(cur))
    return True


def _usable(rec, main_inst) -> bool:
    return rec is not None and rec.inst_id != main_inst and not rec.locked and not rec.player_inst_id


async def handle_level(client, pb: bytes):
    acc = client.account
    req = P.RecallingUpgradeLevelC2S.FromString(pb)
    rec = get(acc, req.inst_id)
    fail = P.RecallingUpgradeLevelS2C(err=P.FAILED).SerializeToString()
    if rec is None:
        return await client.send_packet(MSG_LEVEL, fail)
    if not req.consume_inst_ids and not req.items:  # breakthrough
        step = breakthrough(rec)
        costs: dict[int, int] = {}
        for i, n in (step[1] if step else []):
            costs[i] = costs.get(i, 0) + n
        if step is None or not await _pay(client, costs):
            log.info(f"  RECALLING breakthrough refused {rec.inst_id} {rec.level}/{rec.max_level}")
            return await client.send_packet(MSG_LEVEL, fail)
        rec.max_level = step[0]
        acc.put_recalling(rec.SerializeToString())
        acc.save()
        log.info(f"  RECALLING breakthrough {rec.inst_id} -> max level {rec.max_level}")
        await client.send_packet(MSG_UPDATE, _update(updates=[rec]))
        return await client.send_packet(MSG_LEVEL, P.RecallingUpgradeLevelS2C().SerializeToString())
    eaten = [get(acc, i) for i in dict.fromkeys(req.consume_inst_ids)]
    items: dict[int, int] = {}
    for it in req.items:
        conf = acc.item_by_inst(it.inst_id) or it.conf_id
        items[conf] = items.get(conf, 0) + max(it.number, 0)
    if (rec.level >= rec.max_level or not all(_usable(e, rec.inst_id) for e in eaten)
            or any(i not in EXP_ITEM for i in items)):
        return await client.send_packet(MSG_LEVEL, fail)
    exp_in = sum(LEVELUP.get((star(e), e.level), {}).get("NumExp") or 0 for e in eaten)
    exp_in += sum(EXP_ITEM[i] * n for i, n in items.items())
    gold = sum(STAR_GOLD.get(star(e), 0) for e in eaten) + sum(ITEM_GOLD.get(i, 0) * n for i, n in items.items())
    costs = {**items, GOLD: items.get(GOLD, 0) + gold}
    if not await _pay(client, costs):
        return await client.send_packet(MSG_LEVEL, fail)
    lost = level_up(rec, exp_in)
    acc.put_recalling(rec.SerializeToString())
    for e in eaten:
        acc.remove_recalling(e.inst_id)
    acc.save()
    log.info(f"  RECALLING {rec.inst_id} +{exp_in} exp -> level {rec.level} exp {rec.exp} (lost {lost}), gold {gold}")
    await client.send_packet(MSG_UPDATE, _update(updates=[rec], removes=[e.inst_id for e in eaten]))
    await client.send_packet(MSG_LEVEL, P.RecallingUpgradeLevelS2C().SerializeToString())


async def handle_stage(client, pb: bytes):
    acc = client.account
    req = P.RecallingUpgradeStageC2S.FromString(pb)
    rec = get(acc, req.inst_id)
    fail = P.RecallingUpgradeStageS2C(err=P.FAILED).SerializeToString()
    if rec is None:
        return await client.send_packet(MSG_STAGE, fail)
    eaten = [get(acc, i) for i in dict.fromkeys(req.consume_inst_ids)]
    items: dict[int, int] = {}
    for inst in req.consume_items_inst_ids:
        conf = acc.item_by_inst(inst)
        items[conf] = items.get(conf, 0) + 1
    want = STAGE_ITEM.get(star(rec))
    units = len(eaten) + sum(items.values())
    top = MAX_STAGE.get(star(rec), 5)
    if (not units or rec.stage >= top or any(i != want for i in items)
            or not all(_usable(e, rec.inst_id) and e.conf_id == rec.conf_id for e in eaten)):
        return await client.send_packet(MSG_STAGE, fail)
    new = min(rec.stage + units, top)
    gold = stage_gold(rec, new)
    if not await _pay(client, {**items, GOLD: gold}):
        return await client.send_packet(MSG_STAGE, fail)
    rec.stage = new
    acc.put_recalling(rec.SerializeToString())
    for e in eaten:
        acc.remove_recalling(e.inst_id)
    acc.save()
    log.info(f"  RECALLING {rec.inst_id} stage -> {rec.stage}, gold {gold}")
    await client.send_packet(MSG_UPDATE, _update(updates=[rec], removes=[e.inst_id for e in eaten]))
    await client.send_packet(MSG_STAGE, P.RecallingUpgradeStageS2C().SerializeToString())


async def handle_decomp(client, pb: bytes):
    acc = client.account
    req = P.RecallingDeComposeC2S.FromString(pb)
    recs = [get(acc, i) for i in dict.fromkeys(req.inst_ids)]
    ok = recs and all(r is not None and not r.locked and not r.player_inst_id and r.level <= 1 and r.stage <= 1
                      and star(r) in DECOMP_STARS for r in recs)
    if not ok:
        return await client.send_packet(MSG_DECOMP, P.RecallingDeComposeS2C(err=P.FAILED).SerializeToString())
    n = decomp_reward(recs)
    for r in recs:
        acc.remove_recalling(r.inst_id)
    acc.save()
    log.info(f"  RECALLING dismantle {len(recs)} -> {DECOMP_ITEM} x{n}")
    await client.send_packet(MSG_UPDATE, _update(removes=[r.inst_id for r in recs]))
    if n:
        import pvp
        await pvp.HOOKS["grant_rewards"](client, [(DECOMP_ITEM, n)], True)
    await client.send_packet(MSG_DECOMP, P.RecallingDeComposeS2C().SerializeToString())


def auto_list(acc) -> list[int]:
    if acc.recalling_auto is not None:
        return list(acc.recalling_auto)
    return list(P.RecallingInfoS2C.FromString(acc.b.pb("0x3C01")).setting)


async def handle_auto(client, pb: bytes):
    acc = client.account
    req = P.RecallingAutoResolveC2S.FromString(pb)
    have = auto_list(acc)
    have = [i for i in have if i not in set(req.unids)] + [i for i in req.ids if i not in have and i in RECALLING]
    acc.recalling_auto = have
    acc.save()
    await client.send_packet(MSG_AUTO, P.RecallingAutoResolveS2C(setting=have).SerializeToString())


def info(acc, raw: bytes) -> bytes:
    """0x3C01 at login with this account's auto-dismantle list (memories follow as 0x3C02 updates)."""
    if acc.recalling_auto is None:
        return raw
    m = P.RecallingInfoS2C.FromString(raw)
    del m.setting[:]
    m.setting.extend(acc.recalling_auto)
    return m.SerializeToString()


HANDLERS = {MSG_LEVEL: handle_level, MSG_STAGE: handle_stage, MSG_DECOMP: handle_decomp, MSG_AUTO: handle_auto}
