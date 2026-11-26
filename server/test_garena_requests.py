"""Offline test of the match request as Garena's client sends it (matches.stage_of / request_lineup /
double_cost, server.handle_pvp_create). The request shapes are the two captured ones (practice
[1, 210116, 0], Extreme Training [2, 601]) and the forms the client code builds
(TeamView.FastFormationViewLogic::StartMatch). Temporary accounts, no port.
  python test_garena_requests.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-request-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
STAMINA = server.STAMINA
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

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def args(pvp_type, *pvp_args):
    return P.PVPRoomArgs(pvp_type=pvp_type, pvp_args=list(pvp_args))


async def create(client, pvp_type, *pvp_args):
    """Sends PVPCreateC2S, returns (room info, stamina spent)."""
    before = client.account.get_currency(STAMINA)
    req = P.PVPCreateC2S(args=args(pvp_type, *pvp_args))
    await server.handle_pvp_create(client, req.SerializeToString())
    rep = P.PVPCreateS2C()
    rep.ParseFromString(client.last(0x0302))
    return rep.info, before - client.account.get_currency(STAMINA)


async def main():
    # --- request forms -------------------------------------------------------------------------
    check("practice [1, 210116, 0] -> stage 210116", matches.stage_of(args(P.FIGHT_SOURCE, 1, 210116, 0)) == 210116)
    check("Extreme Training [2, 601] -> stage 100601", matches.stage_of(args(P.FIGHT_CONTEST, 2, 601)) == 100601)
    check("trial [1, 320101] -> stage 320101", matches.stage_of(args(P.FIGHT_SPECIFIC_TRIAL, 1, 320101)) == 320101)
    check("three arguments, not practice: stage is the third", matches.stage_of(args(P.FIGHT_TOKYO, 0, 5, 10101)) == 10101)
    check("line-up of the request", [matches.request_lineup(args(P.FIGHT_SOURCE, 1, 210116, 0)),
                                     matches.request_lineup(args(P.FIGHT_CONTEST, 2, 601))] == [1, 2])
    check("club boss: first argument is no line-up", matches.request_lineup(args(P.FIGHT_ASSOC_BOSS, 1, 2)) is None)
    check("double cost only from the third practice argument",
          [matches.double_cost(args(P.FIGHT_SOURCE, 1, 210116, 0)), matches.double_cost(args(P.FIGHT_SOURCE, 1, 210116, 1)),
           matches.double_cost(args(P.FIGHT_SOURCE, 1, 210116)), matches.double_cost(args(P.FIGHT_CONTEST, 2, 601))]
          == [False, True, False, False])

    # --- line-up named in the request ----------------------------------------------------------
    c = await login(990201)
    acc = c.account
    team = P.TeamInfoS2C()
    team.ParseFromString(acc.team_info())
    forms = {f.lineup: list(f.inst_ids) for f in team.formations}
    saved = {x.fight_type: x.line_up_chosen for x in team.fight_line_up_chosens}
    print(f"     account line-ups {sorted(forms)}, saved per fight type {saved}")
    main_ids = [i for i in forms[sorted(forms)[0]] if i]
    # a second line-up with the same cards in another order, so the two can be told apart
    other = max(forms) + 1
    f2 = team.formations.add()
    f2.CopyFrom(team.formations[0])
    f2.lineup = other
    ids = list(f2.inst_ids)
    ids[0], ids[1] = ids[1], ids[0]
    del f2.inst_ids[:]
    f2.inst_ids.extend(ids)
    acc.team_info = lambda: team.SerializeToString()  # this test account only

    def home_ids(info):
        return [p.info.inst_id for p in info.home.formation.players]

    acc.set_currency(STAMINA, 200) if hasattr(acc, "set_currency") else None
    base_lineup = sorted(forms)[0]
    info, spent = await create(c, P.FIGHT_SOURCE, base_lineup, 210101, 0)
    check("practice with the first line-up: its players", home_ids(info)[:2] == main_ids[:2], f"stamina -{spent}")
    single = spent
    info, spent = await create(c, P.FIGHT_SOURCE, other, 210101, 0)
    check("practice with the other line-up: its players, not the saved ones", home_ids(info)[:2] == ids[:2])
    info, spent = await create(c, P.FIGHT_SOURCE, 77, 210101, 0)
    want = forms.get(saved.get(P.FIGHT_SOURCE), forms[base_lineup])
    check("unknown line-up in the request: falls back to the saved one", home_ids(info)[:2] == [i for i in want if i][:2])
    info, spent = await create(c, P.FIGHT_CONTEST, other, 1)
    check("Extreme Training floor with the other line-up", home_ids(info)[:2] == ids[:2] and matches.stage_of(info.room_args) == 100001)

    # --- double cost ---------------------------------------------------------------------------
    info, spent = await create(c, P.FIGHT_SOURCE, base_lineup, 210101, 1)
    check("practice at double cost: twice the stamina", single > 0 and spent == 2 * single, f"-{single} / -{spent}")
    check("room marked as double", getattr(c.room, "double", False) is True)

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
