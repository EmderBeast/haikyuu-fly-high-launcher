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

    # two harvests (4002005, GainNum [7, 7]): the first one leaves the land growing
    one = all_lands[0]
    g["lands"][str(one.inst_id)] = P.GardenLand(weed_id=4002005, decrease=10000, start_time=now,
                                                gain_times=[now + 10, now + 20], update_time=now + 20).SerializeToString().hex()
    req = P.HomeGardenLandWeedC2S(status=H.HARVEST, inst_id=[one.inst_id])
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 15)
    land = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).furniture[0].land
    check("first of two harvests: 7, land keeps growing", P.NotifyRewardsS2C.FromString(c.sent[1][1]).items[0].number == 7
          and land.weed_id == 4002005 and land.gain_round == 1)
    c.sent.clear()
    await H.handle_land_weed(c, req.SerializeToString(), now=now + 25)
    land = P.HomeGardenLandWeedS2C.FromString(c.sent[-1][1]).furniture[0].land
    check("second harvest: 7 more, land empty", P.NotifyRewardsS2C.FromString(c.sent[1][1]).items[0].number == 7
          and not land.weed_id)

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
          == {(920000104, 96), (920000105, 14)}, f"{crops[-1] if crops else None}")
    garden = [P.HomeUpdateS2C.FromString(p) for mid, p in c2.sent if mid == H.MSG_UPDATE and p]
    lands_now = [f for u in garden for f in u.furnitures if f.conf_id == H.LAND_CONF]
    check("login sends this player's garden", len(lands_now) == 12, f"{len(lands_now)}")
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
