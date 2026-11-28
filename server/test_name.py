"""Offline test of the player name (personal.py, 0x2104 PersonalNameSet): the chosen name comes back in the
reply and in PersonalInfoS2C after a new login, a name another player has gets ROLE_NAME_REPEATED (any
case), an empty one HAS_BAD_CHAR. Tutorial groups (guide.py) start undone and stay done once finished. New players wear the game's default icon and frame. Temporary accounts.
  python test_name.py
"""
import asyncio
import os
import tempfile
from pathlib import Path

import accounts

os.environ["HFH_MULTIPLAYER"] = "1"  # shared server: no creative mode for the new players
TMP = Path(tempfile.mkdtemp(prefix="hfh-name-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import personal  # noqa: E402
import starter  # noqa: E402
import wire  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def info(c):
    return P.PersonalInfoS2C.FromString(next(p for m, p in c.sent if m == 0x2101))


async def rename(c, name):
    c.sent.clear()
    await server.MESSAGE_HANDLERS[0x2104](c, P.PersonalNameSetC2S(name=name).SerializeToString())
    return P.PersonalNameSetS2C.FromString(next(p for m, p in c.sent if m == 0x2104))


async def main():
    a, b = await login(910001), await login(910002)
    look = starter.default_look()
    assert info(a).icon_id == look[4] and info(a).icon_frame_id == look[5], info(a)
    r = await rename(a, "  Emder ")
    assert r.err == 0 and r.name == "Emder" and r.name_set_times == 1, r
    r = await rename(b, "emder")
    assert r.err == P.ROLE_NAME_REPEATED, r
    r = await rename(b, "")
    assert r.err == P.HAS_BAD_CHAR, r
    r = await rename(a, "Emder")  # own name again is fine
    assert r.err == 0, r
    a2 = await login(910001)
    assert info(a2).name == "Emder", info(a2).name
    assert server.player_name(a2) == "Emder"
    # tutorial: a new player starts with only the skipped groups done, finished groups stay done
    import guide
    g = P.UserGuideInfoS2C.FromString(next(p for m, p in a2.sent if m == 0x1F01))
    assert list(g.done_group_ids) == guide.SKIPPED, g
    a2.sent.clear()
    await server.MESSAGE_HANDLERS[0x1F02](a2, P.UserGuideDoneC2S(group_id=1).SerializeToString())
    assert P.UserGuideDoneS2C.FromString(next(p for m, p in a2.sent if m == 0x1F02)).group_id == 1
    a3 = await login(910001)
    g = P.UserGuideInfoS2C.FromString(next(p for m, p in a3.sent if m == 0x1F01))
    assert list(g.done_group_ids) == sorted(guide.SKIPPED + [1]), g
    # guided first recruit (group 3): the pull gives Hinata 1140, the next one is random again
    import gameconfig
    lot = next(i for i in sorted(accounts.CAPTURED.lotteries) if (gameconfig.lottery_cost(i) or (0,))[0] == 318)
    b2 = await login(910003)
    assert guide.RECRUIT_CARD not in b2.account.owned_conf_ids()
    b2.sent.clear()
    await server.MESSAGE_HANDLERS[0x0F02](b2, wire.build([(1, 0, lot), (2, 0, 1)]))
    assert guide.RECRUIT_CARD in b2.account.owned_conf_ids()
    assert guide.forced_draw(910003, b2.account, 1) == []
    # story stage 10101 lends Sugawara (assistbattle 5): he plays in place of the setter
    args = P.PVPRoomArgs(pvp_type=P.FIGHT_TOKYO, pvp_args=[0, 10101])
    room = matches.build_room(args.SerializeToString(), b2.account, 910003, "x")
    home = [p.info.conf_id for p in P.PVPCreateS2C.FromString(room[0]).info.home.formation.players[:7]]
    assert home[0] == 1102, home  # slot 0: serves and sets in the stage's script
    args = P.PVPRoomArgs(pvp_type=P.FIGHT_TOKYO, pvp_args=[0, 10102])
    room = matches.build_room(args.SerializeToString(), b2.account, 910003, "x")
    home = [p.info.conf_id for p in P.PVPCreateS2C.FromString(room[0]).info.home.formation.players[:7]]
    lineup = [P.PlayerCard.FromString(b2.account.get_card(i)).conf_id for i in matches._lineup(b2.account, P.FIGHT_TOKYO)[:7]]
    assert home[0] == 1105 and home[1:6] == lineup[1:6], (home, lineup)  # line-up order kept, Tanaka on the serve place
    # after the match the lent character leaves the line-up (next stage lends another one)
    form = P.TeamInfoS2C.FromString(b2.account.team_info()).formations[0]
    form.assist_position.CopyFrom(P.AssistBattle(conf_id=1102, position=0))
    b2.account.save_formation(form.SerializeToString())
    b2.sent.clear()
    await server.clear_assist(b2)
    assert not any(f.HasField("assist_position") for f in P.TeamInfoS2C.FromString(b2.account.team_info()).formations)
    assert any(m == 0x0B03 for m, _ in b2.sent)
    # an account with the recruit group done but no 1140 gets it at the next login
    b4 = await login(910004)
    guide._store()[str(910004)] = [guide.RECRUIT_GROUP]
    assert guide.RECRUIT_CARD not in b4.account.owned_conf_ids()
    b4 = await login(910004)
    assert guide.RECRUIT_CARD in b4.account.owned_conf_ids()
    print("test_name: OK")


asyncio.run(main())
