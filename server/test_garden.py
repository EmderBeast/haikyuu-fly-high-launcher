"""Offline test of sowing in the home garden (0x231A HomeGardenLandWeedC2S status 0) against the Garena
captures: watering machine and harvest times of all 24 sown lands of two accounts. Temporary accounts, no port.
  python test_garden.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-garden-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import homestead as H  # noqa: E402
import wire  # noqa: E402

P = H.P
CAPTURE = Path(r"D:\2nd Brain\Haikyu-Fly-High-Analyse\capture\decrypted_3\0127_35_2.bin")
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


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def same_as_garena(fs) -> tuple[int, int]:
    """(lands, lands whose machine and harvest times the server computes exactly as Garena sent them)"""
    machines = [f for f in fs if f.conf_id in H.MACHINES]
    lands = [f for f in fs if f.conf_id == H.LAND_CONF and f.land.weed_id]
    good = 0
    for f in lands:
        m = H.watering(f, machines)
        times = H.gain_times(H.WEEDS[f.land.weed_id], f.land.start_time, m)
        if m is not None and m.inst_id == f.land.water_inst_id and times == list(f.land.gain_times) \
                and times[-1] == f.land.update_time:
            good += 1
    return len(lands), good


async def main():
    c = await login(990801)
    homes = H.furnitures(c)
    n, good = same_as_garena(list(homes.values()))
    check("baseline account: machine and harvest times of every sown land", n == 12 and good == 12, f"{good}/{n}")
    if CAPTURE.exists():
        upd = P.HomeUpdateS2C.FromString(CAPTURE.read_bytes()[1:])
        n, good = same_as_garena(list(upd.furnitures))
        check("capture decrypted_3 0127: the same for its 12 lands", n == 12 and good == 12, f"{good}/{n}")

    # sow on two emptied lands (harvesting comes in a later pass)
    lands = [f for f in homes.values() if f.conf_id == H.LAND_CONF][:2]
    g = H._garden(H.entry(c))
    for f in lands:
        g["lands"][str(f.inst_id)] = P.GardenLand(decrease=f.land.decrease).SerializeToString().hex()
    now = 1_800_000_000
    req = P.HomeGardenLandWeedC2S(status=H.SOW, weed_id=4002005)
    req.inst_id.extend(f.inst_id for f in lands)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now)
    check("update first, then the reply", [m for m, _ in c.sent] == [H.MSG_UPDATE, H.MSG_LAND_WEED],
          f"{[hex(m) for m, _ in c.sent]}")
    reply = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1])
    check("reply without error, with the two lands", reply.err == 0 and len(reply.furniture) == 2)
    land = reply.furniture[0].land
    m = H.watering(lands[0], [f for f in homes.values() if f.conf_id in H.MACHINES])
    check("sown: 4002005, harvests at +16800 / +33600 s with its machine",
          land.weed_id == 4002005 and list(land.gain_times) == [now + 16800, now + 33600]
          and land.update_time == now + 33600 and land.water_inst_id == m.inst_id and land.start_time == now,
          str(land).replace("\n", " "))
    upd = P.HomeUpdateS2C.FromString(c.sent[0][1])
    mach = [f for f in upd.furnitures if f.conf_id in H.MACHINES]
    check("the machine runs from sowing until ripe", mach and mach[0].machine.start_time == now
          and mach[0].machine.update_time == now + 33600)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now)
    check("sowing a land that is not empty is refused", P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).err == H.FAILED)

    # harvest as in decrypted_1 0076-0079: 12 lands of 4002004 (GainNum 8) -> 920000104 x96, lands emptied
    all_lands = [f for f in H.furnitures(c).values() if f.conf_id == H.LAND_CONF]
    for f in all_lands:
        g["lands"][str(f.inst_id)] = P.GardenLand(weed_id=4002004, decrease=7000, start_time=now,
                                                  gain_times=[now + 100], update_time=now + 100).SerializeToString().hex()
    req = P.HomeGardenLandWeedC2S(status=H.HARVEST)
    req.inst_id.extend(f.inst_id for f in all_lands)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 50)
    check("not ripe yet: refused", P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).err == H.FAILED)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 200)
    check("order as Garena: pack change, popup, update, reply",
          [m for m, _ in c.sent] == [H.MSG_PACK_CHANGE, 0x0404, H.MSG_UPDATE, H.MSG_LAND_WEED], f"{[hex(m) for m, _ in c.sent]}")
    pack = P.FurniturePackItemChangeS2C.FromString(c.sent[0][1])
    popup = P.NotifyRewardsS2C.FromString(c.sent[1][1])
    check("12 x GainNum 8 = 96 of 920000104", [(i.conf_id, i.number) for i in popup.items] == [(920000104, 96)]
          and pack.gain_items[0].conf_id == 920000104 and pack.gain_items[0].number == 96)
    reply = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1])
    check("lands emptied, decrease kept", reply.status == H.HARVEST and len(reply.furniture) == 12
          and all(not f.land.weed_id and f.land.decrease == 7000 and not f.land.gain_times for f in reply.furniture))

    # one land ripe to the limit, as in the own recording wcy2611 (status 2): 4002002 GainNum [16, 16, 16],
    # GainLimit 32, CostValue 1000 -> 32, 1000 cement, the third round grows again from now (4 h / 1.5)
    one = all_lands[0]
    g["lands"][str(one.inst_id)] = P.GardenLand(weed_id=4002002, decrease=7000, start_time=now - 99999,
                                                speed_total_time_before=9587).SerializeToString().hex()
    c.account.set_currency(H.CEMENT, 1960533)
    req = P.HomeGardenLandWeedC2S(status=H.HARVEST_ONE, inst_id=[one.inst_id], weed_id=4002002)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 15)
    check("order as Garena: cement, pack, popup, update, reply",
          [m for m, _ in c.sent] == [0x0405, H.MSG_PACK_CHANGE, 0x0404, H.MSG_UPDATE, H.MSG_LAND_WEED], f"{[hex(m) for m, _ in c.sent]}")
    reply = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1])
    land = reply.furniture[0].land
    m = H.watering(one, [f for f in H.furnitures(c).values() if f.conf_id in H.MACHINES])
    check("32 eggplants (GainLimit), 1000 cement", P.NotifyRewardsS2C.FromString(c.sent[2][1]).items[0].number == 32
          and c.account.get_currency(H.CEMENT) == 1959533)
    check("third round grows again: round 2, soil 10000, ripe 9600 s after the harvest",
          reply.status == H.HARVEST_ONE and land.weed_id == 4002002 and land.gain_round == 2 and land.decrease == 10000
          and land.start_time == now + 15 and list(land.gain_times) == [now + 15 + 9600]
          and land.water_inst_id == m.inst_id, str(land).replace(chr(10), " "))
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 16)
    check("not ripe again yet: refused", P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).err == H.FAILED)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 15 + 9600)
    land = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).furniture[0].land
    check("last round: 16, land empty, no cement", P.NotifyRewardsS2C.FromString(c.sent[1][1]).items[0].number == 16
          and not land.weed_id and c.sent[0][0] == H.MSG_PACK_CHANGE)

    # repair as in wcy2611: work 19161, running for 51 s -> 78 %, count 22 -> 132 cement, 777638 -> 997638, work 153
    mach = next(f for f in H.furnitures(c).values() if f.conf_id == 4001005)
    t0 = now + 50000
    g["machines"][str(mach.inst_id)] = P.GardenMachine(effect_area=mach.machine.effect_area, work=19161,
                                                       start_time=t0, update_time=t0 + 9621).SerializeToString().hex()
    c.account.set_currency(H.CEMENT, 1958533)
    c.sent.clear()
    await H.handle_repair(c, P.HomeGardenFurnitureRepairC2S(inst_id=mach.inst_id, count=22).SerializeToString(), now=t0 + 51)
    check("repair: cement, record, reply", [m_ for m_, _ in c.sent] == [0x0405, H.MSG_UPDATE, H.MSG_REPAIR])
    rec = P.HomeUpdateS2C.FromString(c.sent[1][1]).repair_records[0]
    rep = P.HomeGardenFurnitureRepairS2C.FromString(c.sent[2][1]).furniture.machine
    check("repair numbers = Garena's (132, 777638 -> 997638, work 153)",
          (rec.number, rec.before, rec.after, rec.conf_id, rep.work) == (132, 777638, 997638, 227, 153)
          and c.account.get_currency(H.CEMENT) == 1958401, f"{rec.number} {rec.before} {rec.after} {rep.work}")
    c.sent.clear()
    await H.handle_repair(c, P.HomeGardenFurnitureRepairC2S(inst_id=mach.inst_id, count=50).SerializeToString(), now=t0 + 52)
    check("repairing more than is missing: refused", P.HomeGardenFurnitureRepairS2C.FromString(c.sent[-1][1]).err == H.FAILED)

    # orders as in wcy2611 (garden level 13): rewards per slot + garden EXP, crops taken, week counter
    data = P.HomeGardenOrderData(last_refresh_time=H._last_change(now), week_order_num=0)
    for weeds in ([(920000103, 46)], [(920000102, 46), (920000104, 8)], [(920000106, 46), (920000101, 38)],
                  [(920000104, 16)]):
        o = data.curr_order_list.add(order_level=13)
        for conf, n in weeds:
            o.weed_list.add(conf_id=conf, number=n)
    data.next_order_list.add(order_level=13).weed_list.add(conf_id=920000107, number=14)
    g["orders"] = data.SerializeToString().hex()
    for conf in (920000101, 920000102, 920000103, 920000104, 920000106):
        H.add_crop(c, conf, 100, now)
    want = {0: [(117, 5), (100, 28500), (280, 23000)], 1: [(117, 5), (102, 19000), (280, 23500)],
            2: [(117, 5), (127, 1340), (280, 22900)], 3: [(117, 18), (280, 24000)]}
    ok = True
    for slot in range(4):
        c.sent.clear()
        await H.handle_order(c, P.HomeGardenOrderFinishC2S(order_id=slot).SerializeToString(), now=now + 60)
        popup = next(P.NotifyRewardsS2C.FromString(b) for m_, b in c.sent if m_ == 0x0404)
        ok &= [(i.conf_id, i.number) for i in popup.items] == want[slot]
        ok &= c.sent[0][0] == H.MSG_PACK_CHANGE and c.sent[-1] == (H.MSG_ORDER, P.HomeGardenOrderFinishS2C(order_id=slot).SerializeToString())
    check("all four orders: Garena's rewards and order of messages", ok)
    crops = H._crops(c)
    check("crops taken (920000103: 100 - 46; 920000104: 96 harvested + 100 - 8 - 16)",
          crops[920000103].number == 54 and crops[920000104].number == 172,
          f"{crops[920000103].number} {crops[920000104].number}")
    d4 = H.orders(c, now + 60)
    check("four finished, week counter 4", all(o.is_finish for o in d4.curr_order_list) and d4.week_order_num == 4)
    c.sent.clear()
    await H.handle_order(c, P.HomeGardenOrderFinishC2S(order_id=0).SerializeToString(), now=now + 61)
    check("an order twice: refused", P.HomeGardenOrderFinishS2C.FromString(c.sent[-1][1]).err == H.FAILED)
    nxt = H.orders(c, H._last_change(now) + 86400)
    check("06:00 UTC: the preview becomes today's orders", len(nxt.curr_order_list) == 1
          and not nxt.curr_order_list[0].is_finish and nxt.curr_order_list[0].weed_list[0].conf_id == 920000107
          and not nxt.next_order_list)

    # uproot: the land is emptied, nothing harvested; an empty land is refused
    g["lands"][str(one.inst_id)] = P.GardenLand(weed_id=4002003, decrease=10000, start_time=now,
                                                gain_times=[now + 99999]).SerializeToString().hex()
    req = P.HomeGardenLandWeedC2S(status=H.UPROOT, inst_id=[one.inst_id], weed_id=4002003)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 30)
    land = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).furniture[0].land
    check("uproot: land empty, no harvest", not land.weed_id and land.decrease == 10000
          and all(m != 0x0404 for m, _ in c.sent))
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 31)
    check("uproot an empty land: refused", P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).err == H.FAILED)

    c2 = await login(990801)
    crops = [P.FurniturePackItemChangeS2C.FromString(p) for mid, p in c2.sent if mid == H.MSG_PACK_CHANGE]
    check("login sends this player's crops", crops and {(i.conf_id, i.number) for i in crops[-1].gain_items}
          == {(k, v.number) for k, v in H._crops(c2).items()} and (920000104, 172) in {(i.conf_id, i.number) for i in crops[-1].gain_items})
    orders_at_login = [u for mid, p_ in c2.sent if mid == H.MSG_UPDATE and p_ for u in [P.HomeUpdateS2C.FromString(p_)] if u.HasField("garden_order")]
    check("login sends this player's orders", orders_at_login and orders_at_login[-1].garden_order.week_order_num == 4)
    garden = [P.HomeUpdateS2C.FromString(p) for mid, p in c2.sent if mid == H.MSG_UPDATE and p]
    lands_now = [f for u in garden for f in u.furnitures if f.conf_id == H.LAND_CONF]
    check("login sends this player's garden", len(lands_now) == 12, f"{len(lands_now)}")
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
