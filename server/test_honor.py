"""Offline test of the Path to Honor (0x3002 HonorRewardC2S, 0x3003 HonorRewardOneKeyC2S): a stage of
honorpath can be claimed once when the trophies (currency 132) have reached its Pathvalue; a bond gives
20 trophies per player once its first task is finished, an owned voice / sticker its UnlockReward; one
key takes everything open on one player's page. The rules are checked against the Garena login capture.
Temporary accounts, no port.
  python test_honor.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-honor-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import honor  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def claim(c, stage, type_id=None):
    """Sends the request; returns (err of the reply or None without a typed reply, stages in HonorInfoS2C sent with it)."""
    start = len(c.sent)
    req = P.HonorRewardC2S(type_id=P.HONOR_MAIN_REWARD if type_id is None else type_id, honor_id=stage)
    await honor.handle_honor_reward(c, req.SerializeToString())
    sent = c.sent[start:]
    stages = []
    for msg_id, payload in sent:
        if msg_id == honor.MSG_INFO:
            info = P.HonorInfoS2C()
            info.ParseFromString(payload)
            stages += list(info.main_rewards)
    reply = next((p for m, p in reversed(sent) if m == honor.MSG_REWARD and p is not None), None)
    err = None
    if reply is not None:
        rep = P.HonorRewardS2C()
        rep.ParseFromString(reply)
        err = rep.err
    return err, stages, [m for m, _ in sent]


async def main():
    c = FakeClient(990701)
    await server.handle_login(c, wire.build([(1, 0, 990701), (7, 2, b"token")]))
    acc = c.account
    check("table loaded", len(honor.PATH) == 300 and all(honor.path_reward(r) for r in honor.PATH.values()), f"{len(honor.PATH)} stages")
    values = [honor.PATH[i]["Pathvalue"] for i in sorted(honor.PATH)]
    check("the path's values only grow", values == sorted(values) and len(set(values)) == len(values))

    stage = min(i for i in honor.PATH if i not in honor.taken(c))
    row = honor.PATH[stage]
    (item, number), = honor.path_reward(row)
    print(f"     stage {stage}: {row['Pathvalue']} trophies -> {number} x item {item}")

    acc.set_currency(honor.TROPHY, row["Pathvalue"] - 1)
    err, stages, _ = await claim(c, stage)
    check("one trophy short: refused, no state sent", err == honor.FAILED and not stages and stage not in honor.taken(c))

    acc.set_currency(honor.TROPHY, row["Pathvalue"])
    before = acc.get_currency(item)
    err, stages, order = await claim(c, stage)
    check("trophies reached: accepted", err == 0)
    check("claimed stage sent as HonorInfoS2C, before the reply", stages == [stage] and order.index(honor.MSG_INFO) < len(order) - 1 and order[-1] == honor.MSG_REWARD, str([hex(m) for m in order]))
    check("trophies are compared, not spent", acc.get_currency(honor.TROPHY) == row["Pathvalue"])
    check("reward credited", acc.get_currency(item) == before + number, f"{before} -> {acc.get_currency(item)}")

    err, stages, _ = await claim(c, stage)
    check("second claim: refused", err == honor.FAILED and not stages)
    err, stages, _ = await claim(c, 99999)
    check("unknown stage: refused", err == honor.FAILED)

    check("kept on the server", stage in pvp.state()["honor"][str(c.gid)])
    login = honor.on_login(c)
    info = P.HonorInfoS2C()
    info.ParseFromString(login[0][1])
    check("sent again at login, merged (all = false)", login[0][0] == honor.MSG_INFO and list(info.main_rewards) == [stage] and not info.all)

    other = FakeClient(990702)
    await server.handle_login(other, wire.build([(1, 0, 990702), (7, 2, b"token")]))
    check("another account is not affected", stage not in honor.taken(other) and honor.on_login(other) == [])

    # --- bonds: 20 trophies per player of the bond, once its first task is finished ---
    bond = next(i for i in sorted(honor.BONDS) if len(honor._list(honor.BONDS[i].get("CharacterID"))) == 2 and honor._list(honor.BONDS[i].get("TaskID")))
    p1, p2 = honor._list(honor.BONDS[bond]["CharacterID"])
    task = honor._list(honor.BONDS[bond]["TaskID"])[0]
    check("bond reward is the constant 10231", honor.BOND_TROPHIES == 20)

    async def ask(type_id, honor_id, player=0):
        start = len(c.sent)
        await honor.handle_honor_reward(c, P.HonorRewardC2S(type_id=type_id, honor_id=honor_id, character_id=player).SerializeToString())
        return replies(c.sent[start:], honor.MSG_REWARD, P.HonorRewardS2C)

    def replies(sent, msg_id, kind):
        info = P.HonorInfoS2C()
        for m, payload in sent:
            if m == honor.MSG_INFO:
                info.MergeFromString(payload)
        rep = kind()
        rep.ParseFromString(next(p for m, p in reversed(sent) if m == msg_id and p is not None))
        return rep.err, info, [m for m, _ in sent]

    err, info, _ = await ask(P.HONOR_TRAMMEL, bond, p1)
    check("bond with an open task: refused", err == honor.FAILED and not info.trammels)
    acc.tasks[task] = wire.build([(1, 0, task), (2, 0, accounts.TASK_FINISH)])
    trophies = acc.get_currency(honor.TROPHY)
    err, info, order = await ask(P.HONOR_TRAMMEL, bond, p1)
    check("bond with a finished task: accepted, 20 trophies", err == 0 and acc.get_currency(honor.TROPHY) == trophies + 20, str(acc.get_currency(honor.TROPHY) - trophies))
    check("the bond's element names the player, before the reply", [(t.trammel_id, list(t.character_id)) for t in info.trammels] == [(bond, [p1])] and order[0] == honor.MSG_INFO and order[-1] == honor.MSG_REWARD)
    err, info, _ = await ask(P.HONOR_TRAMMEL, bond, p1)
    check("same player again: refused", err == honor.FAILED and acc.get_currency(honor.TROPHY) == trophies + 20)
    err, info, _ = await ask(P.HONOR_TRAMMEL, bond, 999999)
    check("a player outside the bond: refused", err == honor.FAILED)
    err, info, _ = await ask(P.HONOR_TRAMMEL, bond, p2)
    check("second player: the element carries both (the client replaces it)", err == 0 and [list(t.character_id) for t in info.trammels] == [[p1, p2]] and acc.get_currency(honor.TROPHY) == trophies + 40)
    school = next(i for i, r in honor.BONDS.items() if not honor._list(r.get("TaskID")))
    err, info, _ = await ask(P.HONOR_TRAMMEL, school, honor._list(honor.BONDS[school]["CharacterID"])[0])
    check("a bond without tasks gives nothing", err == honor.FAILED)

    # --- voices / stickers: the item's UnlockReward once it is owned ---
    import personal  # noqa: E402
    import time  # noqa: E402
    player = p1
    items = honor.player_items(player)
    voice = next(i for i in items if honor.ITEMS[i].get("ItemType") == 8)
    sticker = next(i for i in items if honor.ITEMS[i].get("ItemType") == 7)
    check("a player's page lists its voices and stickers", len(items) > 2 and all(honor.item_trophies(i) in (10, 15) for i in items), f"{len(items)} items")
    err, info, _ = await ask(P.HONOR_PERSONAL, voice)
    check("an item the account does not own: refused", err == honor.FAILED and not info.personals)
    personal.grant(c.gid, [voice], int(time.time()) + 3600)
    trophies = acc.get_currency(honor.TROPHY)
    err, info, _ = await ask(P.HONOR_PERSONAL, voice)
    check("owned voice: accepted, its trophies", err == 0 and list(info.personals) == [voice] and acc.get_currency(honor.TROPHY) == trophies + honor.item_trophies(voice))
    err, info, _ = await ask(P.HONOR_PERSONAL, voice)
    check("voice again: refused", err == honor.FAILED)

    # --- one key: everything open on the player's page ---
    async def one_key(conf_id):
        start = len(c.sent)
        await honor.handle_honor_one_key(c, P.HonorRewardOneKeyC2S(conf_id=conf_id).SerializeToString())
        return replies(c.sent[start:], honor.MSG_ONE_KEY, P.HonorRewardOneKeyS2C)

    other_bonds = [b for b in honor.player_bonds(player) if b != bond][:2]
    for b in other_bonds:
        t = honor._list(honor.BONDS[b]["TaskID"])[0]
        acc.tasks[t] = wire.build([(1, 0, t), (2, 0, accounts.TASK_OVER)])
    personal.grant(c.gid, [sticker], int(time.time()) + 3600)
    trophies = acc.get_currency(honor.TROPHY)
    err, info, order = await one_key(player)
    want = 20 * len(other_bonds) + honor.item_trophies(sticker)
    check("one key: the open bonds and the owned sticker, not what was taken", err == 0 and sorted(t.trammel_id for t in info.trammels) == sorted(other_bonds) and list(info.personals) == [sticker], f"{len(info.trammels)} bonds, {list(info.personals)}")
    check("one key: trophies added up", acc.get_currency(honor.TROPHY) == trophies + want and order.count(honor.MSG_INFO) == 1, f"+{acc.get_currency(honor.TROPHY) - trophies}")
    err, info, _ = await one_key(player)
    check("one key with nothing open: refused", err == honor.FAILED and acc.get_currency(honor.TROPHY) == trophies + want)

    info = P.HonorInfoS2C()
    info.ParseFromString(honor.on_login(c)[0][1])
    check("bonds and items sent again at login", {t.trammel_id: list(t.character_id) for t in info.trammels} == {bond: [p1, p2], **{b: [player] for b in other_bonds}} and sorted(info.personals) == sorted([voice, sticker]) and list(info.main_rewards) == [stage])
    check("another account is still not affected", honor.on_login(other) == [] and not honor.bond_taken(other, bond))

    # --- the Garena login capture: what that account has taken is exactly what these rules allow ---
    class Captured:
        gid = 990703
        account = accounts.Account(990703)
    cap = Captured()
    base = honor._baseline(cap)
    done = {i for i, r in honor.BONDS.items() if honor._list(r.get("TaskID")) and cap.account.task_state(honor._list(r["TaskID"])[0]) > 0}
    check("capture: bonds taken = bonds with a finished first task, each with all its players", {t.trammel_id for t in base.trammels} == done and all(set(t.character_id) == set(honor._list(honor.BONDS[t.trammel_id]["CharacterID"])) for t in base.trammels), f"{len(done)} bonds")
    owned = {i for i in honor.owned_items(cap) if honor.item_trophies(i)}
    check("capture: items taken = owned items with trophies", set(base.personals) == owned, f"{len(owned)} items")
    check("capture: nothing left to claim", not any(honor.bond_claimable(cap, t.trammel_id, ch) for t in base.trammels for ch in t.character_id) and not any(honor.item_claimable(cap, i) for i in owned))

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
