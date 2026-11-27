"""Team Cheers (PlayerSupport) per account: activate / star up, the draw's wish and milestone gifts, and the
cheers' morale in matches.

  0x2504 PlayerSupportUpGradeStarC2S{conf_id = cheer} -> 0x2505 PlayerSupportUpdateS2C{datas[{star_id}]},
         then PlayerSupportUpGradeStarS2C{error, star_id}
  0x0F09 LotterySupportWishC2S{id, item_id}, 0x0F13 LotterySupportGetGiftC2S{id, count}
  login: 0x2501 PlayerSupportInfoS2C (owned cheers), 0x0F07 LotterySupportInfoS2C (draw state)

No capture of these requests exists. Belegt from the client code (decoded/analysis/hotfix_il_full.txt) and
the tables:
  - star_id = cheer * 100 + star * 10 + step, top x60 (supportconf.MaxStar 6); not owned -> the next row is
    cheer*100 (activation), owned -> id + 1 while (id + 1) % 10 != 6 and the row exists, else
    cheer*100 + (star + 1) * 10 (PlayerSupportUtils::NextStarUpCfg, IsSupportStarUpEnough)
  - cost = supportstarupconf.Fragment of that row (pairs 8 = item, 16 = number: the cheer's fragments
    402000xxx or Cheer EXP 131), checked by the client against the bag; no gold
  - the reply alone does not change the star: only 0x2505 does (OnSupportCompose / UpdateSupportCharacter)
  - wish: only pools with supportgachaconf.WishMenu 1; the wish is shown from RecallLotteryUnit.wish
  - gifts: supportgacharewardconfig[SupportGachaRewardID].Cumulative "progress-item-count[-big]", claimable
    when progress <= total_count and not in gift_received (count 0 = all), client InitList
  - morale in matches: Garena's PVPTeamInfo.attr holds one ATTR_MORALE per cheer of formation.player_supports
    with supportstarupconf.SupportAttribute key 7 of its star row (all captured home rooms: 60000 for the
    captured account's x60 cheers; the client sums them into the starting morale)
Annahmen:
  - order 0x0802 (items), 0x2505, reply; the reply carries the new star_id
  - after a wish / gift the unit is pushed as 0x0F07 with is_all false (the replies carry nothing)
Not built (needs a capture): 0x0F08 cheer draws (pity 20 in the table and capture vs. 100 in the rule text,
pools, what duplicates become). The cheers' attribute share on the players (Attribute / SupportAttribute 0-6)
is computed by the client itself; whether Garena puts it into the room's player attributes is open.
"""
import json
import logging

import gameconfig
import matches
import wire

P = matches.P
log = logging.getLogger("hfh")

MSG_INFO, MSG_STAR, MSG_UPDATE = 0x2501, 0x2504, 0x2505
MSG_LOTTERY_INFO, MSG_WISH, MSG_GIFT = 0x0F07, 0x0F09, 0x0F13
MORALE_KEY = 7


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


def _flat(v):
    out = []
    for x in v if isinstance(v, list) else ([] if v is None else [v]):
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def kv(v) -> dict[int, int]:
    """Table KV lists (8 = key, 16 = value; the first key may be left out = 0)."""
    out, key = {}, 0
    flat = _flat(v)
    for tag, val in zip(flat[::2], flat[1::2]):
        if tag == 8:
            key = val
        elif tag == 16:
            out[key] = out.get(key, 0) + val
            key = 0
    return out


CHEERS = {r["ID"]: r for r in _load("supportconf")}
STARUP = {r["ID"]: r for r in _load("supportstarupconf")}
GACHA = {r["ID"]: r for r in _load("supportgachaconf")}
GIFTS = {r["ID"]: r for r in _load("supportgacharewardconfig")}


def next_star(cheer: int, star_id: int | None) -> int | None:
    """The row a star up goes to (None = at the top)."""
    if star_id is None:
        return cheer * 100 if cheer * 100 in STARUP else None
    top = CHEERS.get(cheer, {}).get("MaxStar", 6)
    if (star_id + 1) % 10 != top and star_id + 1 in STARUP:
        return star_id + 1
    star = (star_id // 10) % 10
    nxt = cheer * 100 + (star + 1) * 10
    return nxt if star + 1 <= top and nxt in STARUP else None


def cost(star_id: int) -> dict[int, int]:
    return {i: n for i, n in kv_items(STARUP.get(star_id, {}).get("Fragment")).items() if n > 0}


def kv_items(v) -> dict[int, int]:
    """Fragment lists: 8 = item, 16 = number."""
    out, item = {}, None
    flat = _flat(v)
    for tag, val in zip(flat[::2], flat[1::2]):
        if tag == 8:
            item = val
            out.setdefault(item, 0)
        elif tag == 16 and item is not None:
            out[item] += val
    return out


# ------------------------------------------------------------------ account state
def owned(acc) -> dict[int, int]:
    """cheer -> star_id: the login list (Admin Mode unlock / loaded scan / capture) with this account's changes."""
    import unlockall
    base = P.PlayerSupportInfoS2C.FromString(unlockall.support_info(acc, acc.b.pb("0x2501")))
    have = {d.star_id // 100: d.star_id for d in base.datas}
    have.update({int(k): v for k, v in (acc.cheers or {}).items()})
    return have


def info(acc, raw: bytes) -> bytes:
    """0x2501 at login."""
    import unlockall
    if not acc.cheers:
        return unlockall.support_info(acc, raw)
    return P.PlayerSupportInfoS2C(datas=[P.PlayerSupportData(star_id=s) for s in owned(acc).values()]).SerializeToString()


async def handle_star(client, pb: bytes):
    import recalling  # bag / currency payment
    acc = client.account
    req = P.PlayerSupportUpGradeStarC2S.FromString(pb)
    have = owned(acc)
    target = next_star(req.conf_id, have.get(req.conf_id)) if req.conf_id in CHEERS else None
    if target is None or not await recalling._pay(client, cost(target)):
        log.info(f"  CHEER {req.conf_id} star up refused (now {have.get(req.conf_id)}, next {target})")
        return await client.send_packet(MSG_STAR, P.PlayerSupportUpGradeStarS2C(error=P.FAILED).SerializeToString())
    acc.cheers = {**(acc.cheers or {}), str(req.conf_id): target}
    acc.save()
    log.info(f"  CHEER {req.conf_id} -> {target}")
    await client.send_packet(MSG_UPDATE, P.PlayerSupportUpdateS2C(datas=[P.PlayerSupportData(star_id=target)]).SerializeToString())
    await client.send_packet(MSG_STAR, P.PlayerSupportUpGradeStarS2C(star_id=target).SerializeToString())


def units(acc) -> dict[int, object]:
    out = {u.pool_id: u for u in P.LotterySupportInfoS2C.FromString(acc.b.pb("0x0F07")).lotterys}
    for pool, h in (acc.cheer_lottery or {}).items():
        out[int(pool)] = P.SupportLotteryUnit.FromString(bytes.fromhex(h))
    return out


def _save_unit(acc, unit):
    acc.cheer_lottery = {**(acc.cheer_lottery or {}), str(unit.pool_id): unit.SerializeToString().hex()}


def lottery_info(acc, raw: bytes) -> bytes:
    """0x0F07 at login with this account's units."""
    if not acc.cheer_lottery:
        return raw
    m = P.LotterySupportInfoS2C.FromString(raw)
    mine = units(acc)
    keep = [u for u in m.lotterys if u.pool_id not in mine]
    del m.lotterys[:]
    m.lotterys.extend(keep + list(mine.values()))
    return m.SerializeToString()


async def _push(client, unit):
    await client.send_packet(MSG_LOTTERY_INFO, P.LotterySupportInfoS2C(is_all=False, lotterys=[unit]).SerializeToString())


async def handle_wish(client, pb: bytes):
    acc = client.account
    req = P.LotterySupportWishC2S.FromString(pb)
    unit = units(acc).get(req.id)
    if unit is None or GACHA.get(req.id, {}).get("WishMenu") != 1:
        return await client.send_packet(MSG_WISH, P.LotterySupportWishS2C(err=P.FAILED).SerializeToString())
    unit.wish = req.item_id
    _save_unit(acc, unit)
    acc.save()
    await _push(client, unit)
    await client.send_packet(MSG_WISH, P.LotterySupportWishS2C().SerializeToString())


def milestones(pool: int) -> list[tuple[int, int, int]]:
    row = GIFTS.get(GACHA.get(pool, {}).get("SupportGachaRewardID"), {})
    out = []
    for e in row.get("Cumulative") or []:
        parts = [int(x) for x in str(e).split("-")]
        if len(parts) >= 3:
            out.append((parts[0], parts[1], parts[2]))
    return out


async def handle_gift(client, pb: bytes):
    acc = client.account
    req = P.LotterySupportGetGiftC2S.FromString(pb)
    unit = units(acc).get(req.id)
    due = [m for m in milestones(req.id) if unit is not None and m[0] <= unit.total_count
           and m[0] not in unit.gift_received and (not req.count or m[0] == req.count)]
    if not due:
        return await client.send_packet(MSG_GIFT, P.LotterySupportGetGiftS2C(id=req.id, count=req.count,
                                                                              err=P.FAILED).SerializeToString())
    unit.gift_received.extend(m[0] for m in due)
    _save_unit(acc, unit)
    acc.save()
    import pvp
    await pvp.HOOKS["grant_rewards"](client, [(item, n) for _, item, n in due], True)
    await _push(client, unit)
    await client.send_packet(MSG_GIFT, P.LotterySupportGetGiftS2C(id=req.id, count=req.count).SerializeToString())


def team_morale(team, acc, form):
    """The room's morale entries (PVPTeamInfo.attr ATTR_MORALE) from the line-up's cheers at this account's stars."""
    sup = [s for s in (form.player_supports if form is not None else team.formation.player_supports)]
    have = owned(acc)
    keep = [a for a in team.attr if a.type != P.ATTR_MORALE]
    del team.attr[:]
    team.attr.extend(keep)
    del team.formation.player_supports[:]
    team.formation.player_supports.extend(sup)
    for cheer in sup:
        if cheer in have:
            val = kv(STARUP.get(have[cheer], {}).get("SupportAttribute")).get(MORALE_KEY, 0)
            if val:
                team.attr.add(type=P.ATTR_MORALE, val=val, scaled=10000)


HANDLERS = {MSG_STAR: handle_star, MSG_WISH: handle_wish, MSG_GIFT: handle_gift}


def stat_share(acc, supports) -> dict[int, int]:
    """Stats every player of the team gets from the cheers (client TeamCalculator.AddSupportActiveAttr /
    AddSupportLineUpAttrAndSkill): Attribute of every owned cheer + SupportAttribute (keys 0-6) of the
    line-up's cheers, at this account's stars. Captured rooms: with the coach's 210 per stat this is the
    whole team share on every card (e.g. 3 760 000 + 2 100 000 = 5 860 000 on Set)."""
    have = owned(acc)
    out: dict[int, int] = {}
    for sid in have.values():
        for k, v in kv(STARUP.get(sid, {}).get("Attribute")).items():
            if k < 7:
                out[k] = out.get(k, 0) + v
    for cheer in supports:
        if cheer in have:
            for k, v in kv(STARUP.get(have[cheer], {}).get("SupportAttribute")).items():
                if k < 7:
                    out[k] = out.get(k, 0) + v
    return out
