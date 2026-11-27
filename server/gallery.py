"""Gallery (Illustrated) per account: bonds and CGs seen, collection rewards.

  0x1D01 IllustratedInfoS2C{all, illustrated_awarded, fetter_ids, cg_ids, collect_awarded} at login
  0x1D03 IllustratedFetterSeeC2S{ferrer_id[]} -> {err}     bonds looked at (their red dot goes)
  0x1D04 IllustratedCgSeeC2S{cg_id} -> {err}               CG looked at
  0x1D05 IllustratedCollectRewardsC2S{} -> {err, ids}      every reached collection reward

No capture of these requests exists. Belegt from the client (GalleryIn.GalleryInMessageHandler,
GalleryInUtils) and the captured account:
  - collection value = sum of playergalleryconstantconf[playerlist.Rare].Value1 over the characters the
    account has had (0x0408 records); a playergalleryrewardconf row is reached at CollectValue <= value.
    Captured: value 8240 reaches exactly the 164 rows in collect_awarded
  - the reply of 0x1D05 carries the newly claimed ids; the client adds them and opens its reward window
  - the other replies are only checked for their error
Annahmen:
  - the items of a reached row (GalleryReward, pairs 8 = item, 16 = number) are credited without a second
    pop-up; rows of RewardType 1 (a stat share, [8, 1, 16, 5, 24, 100000]) credit nothing here
  - 0x1D02 (school rewards, galleryreward TargetNum 100-1200) is left as before: its condition is not shown
    by the client and the captured account has claimed none
"""
import json
import logging

import gameconfig
import matches
import wire

P = matches.P
log = logging.getLogger("hfh")

MSG_INFO, MSG_FETTER_SEE, MSG_CG_SEE, MSG_COLLECT = 0x1D01, 0x1D03, 0x1D04, 0x1D05


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


REWARDS = {r["ID"]: r for r in _load("playergalleryrewardconf")}
RARE_VALUE = {r["ID"]: r.get("Value1") or 0 for r in _load("playergalleryconstantconf")}


def _flat(v):
    out = []
    for x in v if isinstance(v, list) else ([] if v is None else [v]):
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def items(row) -> list[tuple[int, int]]:
    if row.get("RewardType") != 2:
        return []
    flat, out = _flat(row.get("GalleryReward")), []
    for tag, val in zip(flat[::2], flat[1::2]):
        if tag == 8:
            out.append([val, 0])
        elif tag == 16 and out:
            out[-1][1] = val
    return [(i, n) for i, n in out if n > 0]


def collect_value(acc) -> int:
    total = 0
    for num, _, v in wire.parse(acc.statistics()):
        if num == 2:
            conf = wire.get(wire.parse(v), 1, 0)
            total += RARE_VALUE.get((gameconfig.PLAYERS.get(conf) or {}).get("Rare"), 0)
    return total


def state(acc):
    """The account's IllustratedInfoS2C (baseline with its changes)."""
    m = P.IllustratedInfoS2C.FromString(acc.b.pb("0x1D01"))
    g = acc.gallery or {}
    for name in ("fetter_ids", "cg_ids", "collect_awarded"):
        have = getattr(m, name)
        have.extend(i for i in g.get(name, []) if i not in have)
    m.all = True
    return m


def _add(acc, name, ids):
    g = dict(acc.gallery or {})
    lst = list(g.get(name, []))
    lst += [i for i in ids if i not in lst]
    g[name] = lst
    acc.gallery = g
    acc.save()


async def handle_fetter_see(client, pb: bytes):
    """Garena capture dizzy2711g (bond 1044 viewed): IllustratedInfoS2C{fetter_ids: [1044]} (all false) first,
    then the empty reply."""
    req = P.IllustratedFetterSeeC2S.FromString(pb)
    _add(client.account, "fetter_ids", list(req.ferrer_id))
    await client.send_packet(MSG_INFO, P.IllustratedInfoS2C(fetter_ids=list(req.ferrer_id)).SerializeToString())
    await client.send_packet(MSG_FETTER_SEE, P.IllustratedFetterSeeS2C().SerializeToString())


async def handle_cg_see(client, pb: bytes):
    req = P.IllustratedCgSeeC2S.FromString(pb)
    _add(client.account, "cg_ids", [req.cg_id])
    await client.send_packet(MSG_CG_SEE, P.IllustratedCgSeeS2C().SerializeToString())


async def handle_collect(client, pb: bytes):
    acc = client.account
    value = collect_value(acc)
    done = set(state(acc).collect_awarded)
    due = [i for i, r in sorted(REWARDS.items()) if i not in done and (r.get("CollectValue") or 0) <= value]
    if not due:
        return await client.send_packet(MSG_COLLECT, P.IllustratedCollectRewardsS2C(err=P.FAILED).SerializeToString())
    _add(acc, "collect_awarded", due)
    gain: dict[int, int] = {}
    for i in due:
        for item, n in items(REWARDS[i]):
            gain[item] = gain.get(item, 0) + n
    log.info(f"  GALLERY collect value {value}: rows {due} -> {gain}")
    import pvp
    await pvp.HOOKS["grant_rewards"](client, sorted(gain.items()), False)
    await client.send_packet(MSG_COLLECT, P.IllustratedCollectRewardsS2C(ids=due).SerializeToString())


def info(acc, raw: bytes) -> bytes:
    """0x1D01 at login."""
    return state(acc).SerializeToString() if acc.gallery else raw


HANDLERS = {MSG_FETTER_SEE: handle_fetter_see, MSG_CG_SEE: handle_cg_see, MSG_COLLECT: handle_collect}
