"""
Practice Match quick clear (0x0306 PVPQuickBattleC2S with pvp_type FIGHT_SOURCE), per player.

Before, it went to the generic reply (the captured arena quick battle): no stamina, no rewards, a wrong result.

Evidenced (Garena capture captures/dizzy2711, dizzy2711h_sweep.json, 28.11.2026: Fukurodani Match 11 = stage 230411,
quick clear x3 at "Practice time Double Drop"):
  - request args {pvp_type 3, pvp_args [line-up 1, stage 230411, 0, count 3]};
  - stamina 15 per run (stage.Cost 105:15): 240 -> 195 for 3;
  - reply PVPQuickBattleS2C.battle_result: the room info (home line-up, 13 players), winner 1, star 7,
    `reward_list` = one ItemList per run with that run's stage Reward rolls as items (boxes 300022-300024
    unopened, manager EXP as 106), `rewards` = the summary of all runs as the reward screen shows it
    (106 shown as 137, items merged), `rewards_decomp` = the potentials the boxes became;
  - the stage Reward rolls ('prob,item,amount', prob / 10000) per run, doubled by the Double Drop event in the
    capture (127 x7000 = 3500 x2, 106 x300 = 150 x2, every item x2);
  - quick clear only for a stage cleared with 3 stars (the client's Quick Clear button, Clear Stars 3).
Assumed: no Double Drop on the server (the event comes from a GM activity, 0x1E02, not built; the runs give the
table's amounts once); the count is limited by the stamina only (the client shows 10 attempts a day, the server
does not count them yet); missions count each run as a finished match of this stage and the stamina as consumed;
boxes are opened with gameconfig.open_rewards as for every other reward (potential boxes: the potentials logic
of the other session decides, rewards_decomp stays empty); order: stamina, rewards, missions, reply.
"""
import random

import gameconfig
import matches
import pvp

P = matches.P
FIGHT_SOURCE = P.FIGHT_SOURCE
STAMINA = 105
SCALE = 10000
MANAGER_EXP, SHOWN_EXP = 106, 137


def roll(stage_id: int, rng=random) -> list[tuple[int, int]]:
    """One run's stage Reward rolls, boxes not opened (as Garena lists them per run)."""
    out = []
    for part in (matches.stage_row(stage_id).get("Reward") or "").split("|"):
        bits = part.split(",")
        if len(bits) == 3 and rng.randrange(SCALE) < int(bits[0]):
            out.append((int(bits[1]), int(bits[2])))
    return out


def shown(items: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Summary as the reward screen lists it: merged, manager EXP 106 shown as 137."""
    out: dict[int, int] = {}
    for item, n in items:
        key = SHOWN_EXP if item == MANAGER_EXP else item
        out[key] = out.get(key, 0) + n
    return list(out.items())


async def handle(client, req, hooks) -> None:
    """hooks: grant_rewards(client, rewards, popup), spend(client, amount) -> bool, mission(client, count, stage)."""
    acc = client.account
    a = list(req.args.pvp_args)
    stage = a[1] if len(a) > 1 else 0
    count = max(1, a[3] if len(a) > 3 else 1)
    cost = gameconfig.stage_cost(stage) or (STAMINA, 15)
    row = matches.stage_row(stage)
    if not row or acc.stage_stars().get(stage, 0) != 0b111 or cost[0] != STAMINA:
        await client.send_packet(pvp.MSG_QUICK, P.PVPQuickBattleS2C(error=1).SerializeToString())
        return
    if not await hooks["spend"](client, cost[1] * count):
        await client.send_packet(pvp.MSG_QUICK, P.PVPQuickBattleS2C(error=1).SerializeToString())
        return
    runs = [roll(stage) for _ in range(count)]
    total = [it for r in runs for it in r]
    await hooks["grant_rewards"](client, gameconfig.open_rewards(total), False)
    await hooks["mission"](client, count, stage)
    out = P.PVPQuickBattleS2C()
    br = out.battle_result
    built = matches.build_room(req.args.SerializeToString(), acc, client.gid, hooks["name"](client))
    if built:
        br.info.CopyFrom(P.PVPCreateS2C.FromString(built[0]).info)
    br.info.room_args.CopyFrom(req.args)
    br.result.winner = matches.HOME_WIN
    br.star = 0b111
    for r in runs:
        lst = br.reward_list.add()
        for item, n in r:
            lst.item.add(conf_id=item, number=n)
    for item, n in shown(total):
        br.rewards.add(conf_id=item, number=n)
    await client.send_packet(pvp.MSG_QUICK, out.SerializeToString())
