"""Potentials (PlayerSpecialty) per account: level up, lock, dismantle, auto-dismantle and filter settings.

  0x1803 PlayerSpecialtyUpLevelC2S{main_inst_id, consume_inst_ids, consume_essence}
  0x1806 PlayerSpecialtyLockC2S{inst_id, lock}
  0x1808 PlayerSpecialtyDecompC2S{inst_ids}
  0x1809 PlayerSpecialtyDecompSetC2S{decomp_auto_set{rare, setting}}
  0x180A PlayerSpecialtyFilterSetingC2S{set_type, set}
  changes: 0x1802 PlayerSpecialtyUpdateS2C{specialtys, opt CREATE 0 / UPDATE 1 / REMOVE 2}

Belegt (no capture of any of these requests exists; client code decoded/analysis/hotfix_il_full.txt,
tables gamedata/cfg, the captured potentials of 0x1801):
  - level up = the client's preview (PlayerSpecialLevelUpViewLogic::UpdateAddSpecialData,
    PlayerSpecialUtility::GetSpecialsWorth): exp = sum of specialtyupconf.Exp of each consumed potential
    (its rare and level) + the essence used (currency 127, 1 exp each). Level by level the exp needed is
    UpExp (minus the exp already held in the first level); each step costs int(exp used / UpExp * UpGold[1])
    gold (currency 100); the consumed potentials' specialtyupconf.Gold is taken off the sum, never below 0.
    The level stops at specialtyfunc 7 [rare - 1] (6, 6, 9, 12, 12).
  - main attribute level = potential level (all 2149 captured potentials)
  - sub-attributes: at the levels of specialtyfunc 11..15 (Value = rare, Data = "3,6,9,12" for rare 4)
    a new one comes while there are fewer than 4, else one of them goes up one level. New ones come from
    the pool specialty.ViceattrPool (specialtyviceattr: Viceattr with Weight), never twice on one potential.
    Captured: level 0 has 3 or 4 sub-attributes, level 12 has 4 with levels adding up to 3 (started with
    3: one came at level 3, three upgrades) or 4 (started with 4: four upgrades); level 6 sums 1 / 2,
    level 9 sums 2; no repeats, all from pool 10, rarer ones (Weight 250) about half as often.
  - dismantle reward = specialtyupconf.Resolve of its rare and level (pairs 8 = item, 16 = number:
    essence 127 x Exp, gold 100 x Gold), as the client's preview reads it
  - PlayerSpecialty field numbers from the captured 0x1801 (5 locked, 6 card, 7 main, 8 subs)
Annahmen:
  - a new sub-attribute or the one going up is picked at random (weights above / evenly)
  - locked or equipped potentials cannot be consumed or dismantled (FAILED); not enough gold/essence: FAILED
  - order: currency changes (0x0405), potential changes (0x1802), then the reply
  - auto-dismantle settings are only stored and sent back (what Garena dismantles with them on its own is
    not recorded); filter schemes get an id from the time and are stored per account
Not done (needs a capture): 0x1807 TempDraw (temporary potentials of PlayerSpecialtyInfoS2C.item_piles),
0x0806 PackSpecialtyItemUse.
"""
import json
import logging
import random
import time

import gameconfig
import matches

P = matches.P
log = logging.getLogger("hfh")

MSG_UPDATE = 0x1802
MSG_UP_LEVEL, MSG_LOCK, MSG_DECOMP, MSG_DECOMP_SET, MSG_FILTER = 0x1803, 0x1806, 0x1808, 0x1809, 0x180A
GOLD, ESSENCE = 100, 127  # PlayerSpecialUtility.get_GoldCount / get_CreamCount
CREATE, UPDATE, REMOVE = 0, 1, 2


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


ROWS = {r["ID"]: r for r in _load("specialty")}
UP = {(r["Rare"], r.get("Level", 0)): r for r in _load("specialtyupconf")}
FUNC = {r["ID"]: r for r in _load("specialtyfunc")}
POOLS = {r["ID"]: r for r in _load("specialtyviceattr")}
MAX_VICES = 4


def _list(v):
    return v if isinstance(v, list) else ([] if v is None else [v])


def max_level(rare: int) -> int:
    levels = _list(FUNC.get(7, {}).get("Value"))
    return levels[rare - 1] if 0 < rare <= len(levels) else 0


def vice_levels(rare: int) -> list[int]:
    """Levels at which a sub-attribute comes or goes up (specialtyfunc rows with Value = rare)."""
    for fid in range(11, 16):
        row = FUNC.get(fid, {})
        if row.get("Value") == rare and row.get("Data"):
            return [int(x) for x in str(row["Data"]).split(",") if x.strip()]
    return []


def rare_of(spec) -> int:
    return ROWS.get(spec.conf_id, {}).get("Rare", 0)


def worth(spec) -> tuple[int, int]:
    """(exp, gold) a potential is worth when consumed (PlayerSpecialUtility::GetSpecialsWorth)."""
    row = UP.get((rare_of(spec), spec.level), {})
    return row.get("Exp") or 0, row.get("Gold") or 0


def resolve(spec) -> list[tuple[int, int]]:
    """Dismantle reward: specialtyupconf.Resolve, flattened pairs 8 = item, 16 = number."""
    flat, out, item = [], [], None

    def walk(v):
        for x in _list(v):
            walk(x) if isinstance(x, list) else flat.append(x)
    walk(UP.get((rare_of(spec), spec.level), {}).get("Resolve"))
    for key, val in zip(flat[::2], flat[1::2]):
        if key == 8:
            item = val
            out.append([val, 0])
        elif key == 16 and item is not None:
            out[-1][1] = val
    return [(i, n) for i, n in out if n > 0]


def level_up(spec, exp_in: int, gold_back: int, rng=random) -> int:
    """Adds exp_in to the potential (levels, main and sub-attributes); returns the gold it costs."""
    rare = rare_of(spec)
    top = max_level(rare)
    gold, first = -gold_back, True
    ups = vice_levels(rare)
    while exp_in > 0 and spec.level < top:
        row = UP.get((rare, spec.level))
        if not row or not row.get("UpExp"):
            break
        price = (_list(row.get("UpGold")) + [0, 0])[1]
        need = row["UpExp"] - (spec.exp if first else 0)
        first = False
        if exp_in < need:
            spec.exp += exp_in
            gold += int(exp_in / row["UpExp"] * price)
            exp_in = 0
            break
        exp_in -= need
        gold += int(need / row["UpExp"] * price)
        spec.level += 1
        spec.exp = 0
        spec.main_attr.attr_lev = spec.level
        if spec.level in ups:
            _vice_step(spec, rng)
    if spec.level >= top:
        spec.exp = 0
    return max(gold, 0)


def _vice_step(spec, rng):
    if len(spec.vice_attrs) < MAX_VICES:
        pool = POOLS.get(ROWS.get(spec.conf_id, {}).get("ViceattrPool"), {})
        have = {v.attr_id for v in spec.vice_attrs}
        cand = [(a, w) for a, w in zip(_list(pool.get("Viceattr")), _list(pool.get("Weight"))) if a not in have and w > 0]
        if cand:
            pick = rng.choices([a for a, _ in cand], weights=[w for _, w in cand])[0]
            spec.vice_attrs.add(attr_id=pick)
            return
    if spec.vice_attrs:
        v = rng.choice(list(spec.vice_attrs))
        v.attr_lev += 1


# ------------------------------------------------------------------ account access
def get(acc, inst: int):
    raw = None if inst in acc.removed_specialties else acc.get_specialty(inst)
    return P.PlayerSpecialty.FromString(raw) if raw else None


def put(acc, spec):
    acc.put_specialty(spec.SerializeToString())


def remove(acc, inst: int):
    acc.specialties.pop(inst, None)
    acc.removed_specialties.add(inst)


def update_msg(specs, opt: int) -> bytes:
    m = P.PlayerSpecialtyUpdateS2C(opt=opt)
    for s in specs:
        m.specialtys.add().CopyFrom(s)
    return m.SerializeToString()


async def _currencies(client, changes: dict[int, int]):
    """Sets the currencies (absolute) and sends NotifyCurrencyChangeS2C."""
    import wire
    from accounts import MSG_CURRENCY_CHANGE
    acc = client.account
    entries = [(1, 2, acc.set_currency(cid, value)) for cid, value in changes.items()]
    if entries:
        await client.send_packet(MSG_CURRENCY_CHANGE, wire.build(entries))


def _err(cls, field="err"):
    return cls(**{field: P.FAILED}).SerializeToString()


# ------------------------------------------------------------------ handlers
async def handle_up_level(client, pb: bytes, rng=random):
    acc = client.account
    req = P.PlayerSpecialtyUpLevelC2S.FromString(pb)
    main = get(acc, req.main_inst_id)
    eaten = [get(acc, i) for i in dict.fromkeys(req.consume_inst_ids) if i != req.main_inst_id]
    essence = max(req.consume_essence, 0)
    ok = (main is not None and all(e is not None and not e.locked and not e.player_inst_id for e in eaten)
          and (eaten or essence) and acc.get_currency(ESSENCE) >= essence
          and main.level < max_level(rare_of(main)))
    if not ok:
        log.info(f"  SPECIALTY up-level refused: {req}")
        await client.send_packet(MSG_UP_LEVEL, _err(P.PlayerSpecialtyUpLevelS2C))
        return
    exp_in = sum(worth(e)[0] for e in eaten) + essence
    gold_back = sum(worth(e)[1] for e in eaten)
    before = main.SerializeToString()
    gold = level_up(main, exp_in, gold_back, rng)
    if acc.get_currency(GOLD) < gold:
        main = P.PlayerSpecialty.FromString(before)
        log.info(f"  SPECIALTY up-level: {gold} gold needed")
        await client.send_packet(MSG_UP_LEVEL, _err(P.PlayerSpecialtyUpLevelS2C))
        return
    put(acc, main)
    for e in eaten:
        remove(acc, e.inst_id)
    acc.save()
    log.info(f"  SPECIALTY {main.inst_id} +{exp_in} exp -> level {main.level} exp {main.exp}, gold {gold}")
    await _currencies(client, {**({GOLD: acc.get_currency(GOLD) - gold} if gold else {}),
                               **({ESSENCE: acc.get_currency(ESSENCE) - essence} if essence else {})})
    if eaten:
        await client.send_packet(MSG_UPDATE, update_msg(eaten, REMOVE))
    await client.send_packet(MSG_UPDATE, update_msg([main], UPDATE))
    await client.send_packet(MSG_UP_LEVEL, P.PlayerSpecialtyUpLevelS2C().SerializeToString())


async def handle_lock(client, pb: bytes):
    acc = client.account
    req = P.PlayerSpecialtyLockC2S.FromString(pb)
    spec = get(acc, req.inst_id)
    if spec is None:
        await client.send_packet(MSG_LOCK, _err(P.PlayerSpecialtyLockS2C))
        return
    spec.locked = req.lock
    put(acc, spec)
    acc.save()
    await client.send_packet(MSG_UPDATE, update_msg([spec], UPDATE))
    await client.send_packet(MSG_LOCK, P.PlayerSpecialtyLockS2C().SerializeToString())


async def handle_decomp(client, pb: bytes):
    acc = client.account
    req = P.PlayerSpecialtyDecompC2S.FromString(pb)
    specs = [get(acc, i) for i in dict.fromkeys(req.inst_ids)]
    if not specs or any(s is None or s.locked or s.player_inst_id for s in specs):
        await client.send_packet(MSG_DECOMP, _err(P.PlayerSpecialtyDecompS2C))
        return
    gain: dict[int, int] = {}
    for s in specs:
        for item, n in resolve(s):
            gain[item] = gain.get(item, 0) + n
        remove(acc, s.inst_id)
    acc.save()
    log.info(f"  SPECIALTY dismantle {len(specs)} -> {gain}")
    await client.send_packet(MSG_UPDATE, update_msg(specs, REMOVE))
    import pvp
    await pvp.HOOKS["grant_rewards"](client, sorted(gain.items()), True)
    await client.send_packet(MSG_DECOMP, P.PlayerSpecialtyDecompS2C().SerializeToString())


def decomp_sets(acc) -> list:
    """Auto-dismantle settings: the account's, else the baseline's (PlayerSpecialtyInfoS2C field 3)."""
    if acc.specialty_settings.get("decomp") is not None:
        return [P.PlayerSpecialtDecompSet(rare=r, setting=s) for r, s in acc.specialty_settings["decomp"]]
    base = P.PlayerSpecialtyInfoS2C.FromString(acc.b.pb("0x1801"))
    return list(base.decomp_auto_set)


async def handle_decomp_set(client, pb: bytes):
    acc = client.account
    req = P.PlayerSpecialtyDecompSetC2S.FromString(pb)
    sets = {s.rare: s.setting for s in decomp_sets(acc)}
    sets[req.decomp_auto_set.rare] = req.decomp_auto_set.setting
    acc.specialty_settings["decomp"] = sorted(sets.items())
    acc.save()
    await client.send_packet(MSG_DECOMP_SET, P.PlayerSpecialtyDecompSetS2C(
        decomp_auto_set=req.decomp_auto_set).SerializeToString())


def filters(acc) -> list:
    if acc.specialty_settings.get("filters") is not None:
        return [P.PlayerSpecialtyFilterSeting.FromString(bytes.fromhex(h)) for h in acc.specialty_settings["filters"]]
    return list(P.PlayerSpecialtyInfoS2C.FromString(acc.b.pb("0x1801")).filter_seting)


async def handle_filter(client, pb: bytes):
    acc = client.account
    req = P.PlayerSpecialtyFilterSetingC2S.FromString(pb)
    have = filters(acc)
    s = P.PlayerSpecialtyFilterSeting()
    s.CopyFrom(req.set)
    if req.set_type == P.FilterSeting_Add:
        s.id = max([f.id for f in have] + [int(time.time() * 1000) << 20]) + 1
        s.create = int(time.time())
        have.append(s)
    elif req.set_type == P.Del:
        have = [f for f in have if f.id != s.id]
    else:  # Update (and Defend): replace the scheme with that id
        old = next((f for f in have if f.id == s.id), None)
        if old is None:
            await client.send_packet(MSG_FILTER, P.PlayerSpecialtyFilterSetingS2C(err=P.FAILED).SerializeToString())
            return
        s.create = s.create or old.create
        have = [s if f.id == s.id else f for f in have]
    acc.specialty_settings["filters"] = [f.SerializeToString().hex() for f in have]
    acc.save()
    await client.send_packet(MSG_FILTER, P.PlayerSpecialtyFilterSetingS2C(
        set_type=req.set_type, set=s).SerializeToString())


def info(acc, raw: bytes) -> bytes:
    """0x1801 at login: the baseline without the potentials this account used up, with its settings."""
    if not (acc.removed_specialties or acc.specialty_settings):
        return raw
    m = P.PlayerSpecialtyInfoS2C.FromString(raw)
    keep = [s for s in m.specialtys if s.inst_id not in acc.removed_specialties]
    del m.specialtys[:]
    m.specialtys.extend(keep)
    if acc.specialty_settings.get("decomp") is not None:
        del m.decomp_auto_set[:]
        m.decomp_auto_set.extend(decomp_sets(acc))
    if acc.specialty_settings.get("filters") is not None:
        del m.filter_seting[:]
        m.filter_seting.extend(filters(acc))
    return m.SerializeToString()


HANDLERS = {MSG_UP_LEVEL: handle_up_level, MSG_LOCK: handle_lock, MSG_DECOMP: handle_decomp,
            MSG_DECOMP_SET: handle_decomp_set, MSG_FILTER: handle_filter}
