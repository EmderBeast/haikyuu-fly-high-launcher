"""
Path to Honor (GloryRoad): the path's stage rewards and the trophies a player's page gives.
  0x3002 HonorRewardC2S{type_id, honor_id, character_id}
      HONOR_MAIN_REWARD  a stage of the path (honorpath ID)
      HONOR_TRAMMEL      a bond's trophies for one of its players (trammel ID, player config id)
      HONOR_PERSONAL     the trophies of an owned voice / sticker of a player (playerinfoitemconfig ID)
  0x3003 HonorRewardOneKeyC2S{conf_id}: everything open on one player's page

No capture holds the exchanges themselves, so the requests follow the client's code
(decoded/analysis/hotfix_il_full.txt); the state they lead to is checked against the Garena login
capture (HonorInfoS2C of the captured account).

Stages (table honorpath, 300 stages: Pathvalue, Pathreward):
  - the path's progress is the Trophy currency (132): GloryRoadMessageHandler::OnCurrencySync /
    OnCurrencyChanged copy that currency into GloryRoadData.totalScore
  - a stage is reached when totalScore >= Pathvalue and taken when its id is in
    HonorInfoS2C.main_rewards (GloryRoadData::UpdateRewardsState, get_MaxUnlockIndex)
  - the client asks with RequestHonorReward(HONOR_MAIN_REWARD, honorpath ID, 0)
  - HonorInfoS2C with all = false is merged: main_rewards are added to the ones the client has
    (GloryRoadData::UpdateData), so the claimed id is sent that way

Bonds (table trammel):
  - claimable when the bond's first task (TaskID[0]) is finished or claimed
    (GloryPlayerTrammel::GetRewardState -> FetterClient::get_TasksAreFinished -> TaskUtils::GetTaskStage)
    and taken per player: HonorInfoS2C.trammels = TrammelElem{trammel_id, character_id[]}
    (GloryRoadData::GotTrammelReward)
  - the reward is the constant 10231 = "132_20": 20 trophies (GloryPlayerTrammel::.ctor)
  - a merged HonorInfoS2C replaces the bond's whole element (GloryRoadData::UpdateData), so the element
    is sent with every player claimed so far
  - capture: the 143 bonds of the captured account whose first task is finished are exactly the 143 in
    HonorInfoS2C.trammels, each with all of its players (also players the account does not hold now);
    the 54 unfinished ones and the 8 school bonds (Type 3, no tasks) are absent. So: no check that the
    player is held, and no reward for bonds without tasks.

Voices and stickers (table playerinfoitemconfig, UnlockReward = [8, 132, 16, number], 10 or 15):
  - claimable when the item is owned (PersonalInfoS2C.item_ids) and taken when its id is in
    HonorInfoS2C.personals (GloryPlayerVoice / Face / Scene::GetRewardState); personals are added on merge
  - capture: the 905 owned items with such a reward are exactly the 905 in HonorInfoS2C.personals

One key: the client only sends the player (GloryPlayerDetailViewLogic::OnAllGetBtnEvent). A player's
page is built from playerlist (GloryPlayer::.ctor): the bonds with that player, Voice, and
PlayerBattleStickerID (sticker item -> item.Itemresult = playerinfoitemconfig ID).

Assumptions (the exchanges were never recorded):
  - the trophies are compared, not spent (the path's values only grow: 100, 200, ... 50000)
  - order: HonorInfoS2C, the reward's own messages with the reward window, then the reply
  - a reward that cannot be claimed is answered with FAILED
  - one key covers bonds, voices and stickers; the player's challenge tasks (PlayerChallengeTask) are
    ordinary missions with their own request and are not claimed by it
  - one key sends one HonorInfoS2C and one reward window with the trophies added up
"""
import logging

import gameconfig
import matches
import pvp

P = matches.P
log = logging.getLogger("hfh")

MSG_INFO, MSG_REWARD, MSG_ONE_KEY = 0x3001, 0x3002, 0x3003
TROPHY = 132
FAILED = 1
BOND_REWARD_CONSTANT = 10231  # "132_20"


def _load(name: str) -> dict:
    return gameconfig._table(name) if gameconfig.AVAILABLE and (gameconfig.CFG / f"{name}.json").exists() else {}


PATH = _load("honorpath")
BONDS = _load("trammel")
ITEMS = _load("playerinfoitemconfig")
PLAYERS = _load("playerlist")
_GAME_ITEMS = _load("item")


def _bond_trophies() -> int:
    data = str(_load("constant").get(BOND_REWARD_CONSTANT, {}).get("Data") or "")
    kind, _, number = data.partition("_")
    return int(number) if kind == str(TROPHY) and number.isdigit() else 0


BOND_TROPHIES = _bond_trophies()


def _pairs(v) -> list[tuple[int, int]]:
    """One or more items as a flat message: [8, item, 16, number] (field 1 = item, field 2 = number)."""
    v = v if isinstance(v, list) else []
    out = []
    for i in range(0, len(v) - 3, 4):
        if v[i] == 8 and v[i + 2] == 16:
            out.append((v[i + 1], v[i + 3]))
    return out


def path_reward(row: dict) -> list[tuple[int, int]]:
    return _pairs(row.get("Pathreward"))


def _list(v) -> list[int]:
    return [x for x in v if isinstance(x, int)] if isinstance(v, list) else ([v] if isinstance(v, int) and v > 0 else [])


def _thirds(v) -> list[int]:
    """Third value of every {1, 2, 3} triple of a table list. The tables hold the first entry as a nested
    list and the rest flat: [[8, a, 16, b, 24, c], 8, a, 16, b, 24, c, ...]."""
    if not isinstance(v, list):
        return []
    flat = []
    for x in v:
        flat += x if isinstance(x, list) else [x]
    return [flat[i + 5] for i in range(0, len(flat) - 5, 6) if flat[i] == 8 and flat[i + 2] == 16 and flat[i + 4] == 24]


def item_trophies(item_id: int) -> int:
    """Trophies of a voice / sticker (UnlockReward = [8, 132, 16, number]); 0 for items without them."""
    return sum(n for i, n in _pairs(ITEMS.get(item_id, {}).get("UnlockReward")) if i == TROPHY)


def player_items(conf_id: int) -> list[int]:
    """Voices and stickers on a player's page (GloryPlayer::.ctor), as playerinfoitemconfig ids."""
    row = PLAYERS.get(conf_id, {})
    stickers = [_GAME_ITEMS.get(i, {}).get("Itemresult") for i in _thirds(row.get("PlayerBattleStickerID"))]
    return [i for i in _thirds(row.get("Voice")) + stickers if isinstance(i, int) and i in ITEMS]


def player_bonds(conf_id: int) -> list[int]:
    return [i for i, r in BONDS.items() if conf_id in _list(r.get("CharacterID")) and _list(r.get("TaskID"))]


def _store(client, key: str, default):
    return pvp.state().setdefault(key, {}).setdefault(str(client.gid), default)


def _mine(client) -> list[int]:
    return pvp.state().get("honor", {}).get(str(client.gid), [])


def _baseline(client):
    info = P.HonorInfoS2C()
    try:
        info.ParseFromString(client.account.b.pb("0x3001"))
    except Exception:  # a baseline without the message
        pass
    return info


def taken(client) -> set[int]:
    """Stages already claimed: the baseline's HonorInfoS2C.main_rewards plus this account's."""
    return set(_baseline(client).main_rewards) | set(_mine(client))


def claimable(client, stage: int) -> bool:
    row = PATH.get(stage)
    return bool(row) and stage not in taken(client) and client.account.get_currency(TROPHY) >= (row.get("Pathvalue") or 0)


def bond_taken(client, bond: int) -> list[int]:
    """Players a bond's trophies were claimed for: baseline plus this account's, in that order."""
    out = [c for t in _baseline(client).trammels if t.trammel_id == bond for c in t.character_id]
    for c in pvp.state().get("honor_bonds", {}).get(str(client.gid), {}).get(str(bond), []):
        if c not in out:
            out.append(c)
    return out


def bond_claimable(client, bond: int, conf_id: int) -> bool:
    row = BONDS.get(bond, {})
    tasks = _list(row.get("TaskID"))
    return (BOND_TROPHIES > 0 and bool(tasks) and conf_id in _list(row.get("CharacterID"))
            and client.account.task_state(tasks[0]) > 0 and conf_id not in bond_taken(client, bond))


def owned_items(client) -> set[int]:
    """Profile items of the account: PersonalInfoS2C.item_ids plus the ones unlocked here."""
    import personal
    out = set(personal.unlocks(client.gid))
    try:
        out |= set(P.PersonalInfoS2C.FromString(client.account.b.pb("0x2101")).item_ids)
    except Exception:
        pass
    return out


def items_taken(client) -> set[int]:
    return set(_baseline(client).personals) | set(pvp.state().get("honor_items", {}).get(str(client.gid), []))


def item_claimable(client, item_id: int) -> bool:
    return item_trophies(item_id) > 0 and item_id in owned_items(client) and item_id not in items_taken(client)


def _take_bond(client, bond: int, conf_id: int):
    """Marks the bond's trophies taken for the player; returns the element the client replaces its own with."""
    _store(client, "honor_bonds", {}).setdefault(str(bond), []).append(conf_id)
    return P.TrammelElem(trammel_id=bond, character_id=bond_taken(client, bond))


def _take_item(client, item_id: int):
    _store(client, "honor_items", []).append(item_id)


async def _finish(client, info, trophies: int, msg_id: int, reply):
    pvp.save()
    await client.send_packet(MSG_INFO, info.SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, [(TROPHY, trophies)], True)
    await client.send_packet(msg_id, reply.SerializeToString())


async def handle_honor_reward(client, pb: bytes):
    req = P.HonorRewardC2S.FromString(pb)
    what = f"type {req.type_id} id {req.honor_id} player {req.character_id}"
    if req.type_id == P.HONOR_TRAMMEL and bond_claimable(client, req.honor_id, req.character_id):
        elem = _take_bond(client, req.honor_id, req.character_id)
        await _finish(client, P.HonorInfoS2C(trammels=[elem]), BOND_TROPHIES, MSG_REWARD, P.HonorRewardS2C())
        log.info(f"  HONOR bond {what}: {BOND_TROPHIES} trophies")
        return
    if req.type_id == P.HONOR_PERSONAL and item_claimable(client, req.honor_id):
        _take_item(client, req.honor_id)
        trophies = item_trophies(req.honor_id)
        await _finish(client, P.HonorInfoS2C(personals=[req.honor_id]), trophies, MSG_REWARD, P.HonorRewardS2C())
        log.info(f"  HONOR item {what}: {trophies} trophies")
        return
    stage = req.honor_id
    if req.type_id != P.HONOR_MAIN_REWARD or not claimable(client, stage):
        log.info(f"  HONOR {what}: refused")
        await client.send_packet(MSG_REWARD, P.HonorRewardS2C(err=FAILED).SerializeToString())
        return
    pvp.state().setdefault("honor", {}).setdefault(str(client.gid), []).append(stage)
    pvp.save()
    rewards = path_reward(PATH[stage])
    await client.send_packet(MSG_INFO, P.HonorInfoS2C(main_rewards=[stage]).SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, True)
    await client.send_packet(MSG_REWARD, P.HonorRewardS2C().SerializeToString())
    log.info(f"  HONOR stage {stage}: {rewards}")


async def handle_honor_one_key(client, pb: bytes):
    """Everything open on one player's page: its bonds, voices and stickers."""
    conf_id = P.HonorRewardOneKeyC2S.FromString(pb).conf_id
    bonds = [b for b in player_bonds(conf_id) if bond_claimable(client, b, conf_id)]
    items = [i for i in dict.fromkeys(player_items(conf_id)) if item_claimable(client, i)]
    if not bonds and not items:
        log.info(f"  HONOR one key player {conf_id}: nothing open")
        await client.send_packet(MSG_ONE_KEY, P.HonorRewardOneKeyS2C(err=FAILED).SerializeToString())
        return
    info = P.HonorInfoS2C(personals=items)
    for b in bonds:
        info.trammels.append(_take_bond(client, b, conf_id))
    for i in items:
        _take_item(client, i)
    trophies = BOND_TROPHIES * len(bonds) + sum(item_trophies(i) for i in items)
    await _finish(client, info, trophies, MSG_ONE_KEY, P.HonorRewardOneKeyS2C())
    log.info(f"  HONOR one key player {conf_id}: {len(bonds)} bonds, {len(items)} items, {trophies} trophies")


def on_login(client) -> list[tuple[int, bytes]]:
    """What was claimed here, on top of the baseline HonorInfoS2C (the client merges it into its lists)."""
    gid = str(client.gid)
    info = P.HonorInfoS2C(main_rewards=_mine(client), personals=pvp.state().get("honor_items", {}).get(gid, []))
    for bond in pvp.state().get("honor_bonds", {}).get(gid, {}):
        info.trammels.add(trammel_id=int(bond), character_id=bond_taken(client, int(bond)))
    return [(MSG_INFO, info.SerializeToString())] if info.ByteSize() else []


HANDLERS = {MSG_REWARD: handle_honor_reward, MSG_ONE_KEY: handle_honor_one_key}
