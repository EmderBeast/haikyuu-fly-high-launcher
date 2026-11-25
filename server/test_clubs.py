"""Offline test of the clubs (clubs.py): create (300 diamonds), list / search, join an open club,
request + approve in a reviewed club, roles, notice, daily welfare, club chat, kick, leave, hand over,
disband. Temporary accounts.
  python test_clubs.py
"""
import asyncio
import tempfile
import time
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-clubs-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import chat  # noqa: E402
import clubs as C  # noqa: E402
import wire  # noqa: E402

P = matches.P


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def all(self, msg_id):
        return [p for m, p in self.sent if m == msg_id]

    def last(self, msg_id):
        return self.all(msg_id)[-1]

    def info(self):
        return P.AssociationInfoS2C.FromString(self.last(C.MSG_INFO))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


async def call(c, msg_id, msg=b""):
    await server.MESSAGE_HANDLERS[msg_id](c, msg if isinstance(msg, bytes) else msg.SerializeToString())
    await asyncio.sleep(0.05)  # pushes run as tasks


def err(c, msg_id, cls):
    return getattr(P, cls).FromString(c.last(msg_id)).err


async def main():
    a, b, c = await login(9821), await login(9822), await login(9823)
    assert not a.info().HasField("association")

    # create: costs 300 diamonds
    a.account.set_currency(C.CREATE_COST[0], 500)
    await call(a, C.MSG_CREATE, P.AssociationCreateC2S(name="Karasuno", read_str="Fly!", icon_id=2))
    assert err(a, C.MSG_CREATE, "AssociationCreateS2C") == 0
    assert a.account.get_currency(C.CREATE_COST[0]) == 200
    club = a.info().association
    print("club", club.id, club.name, club.cur_count, club.leader_name)
    assert club.name == "Karasuno" and club.cur_count == 1 and club.icon_id == 2
    await call(b, C.MSG_CREATE, P.AssociationCreateC2S(name="karasuno"))  # name taken / no diamonds
    assert err(b, C.MSG_CREATE, "AssociationCreateS2C") != 0

    await call(b, C.MSG_LIST)
    assert [o.name for o in P.AssociationListS2C.FromString(b.last(C.MSG_LIST)).association] == ["Karasuno"]
    await call(b, C.MSG_SELECT, P.AssociationSelectC2S(condtion=P.ASSOCIATION_NAME, name="kara"))
    assert P.AssociationSelectS2C.FromString(b.last(C.MSG_SELECT)).association[0].id == club.id
    await call(b, C.MSG_SELECT, P.AssociationSelectC2S(condtion=P.ASSOCIATION_ID, value=club.id))
    assert P.AssociationSelectS2C.FromString(b.last(C.MSG_SELECT)).association[0].id == club.id

    # open club: b joins at once, a sees the new member
    await call(b, C.MSG_APPLY, P.AssociationApplyC2S(association_ids=[club.id]))
    assert P.AssociationApplyS2C.FromString(b.last(C.MSG_APPLY)).is_add
    assert {n.base.id for n in a.info().numbers} == {9821, 9822} and b.info().association.id == club.id

    # reviewed club: c's request waits, a approves
    await call(a, C.MSG_REVIEW, P.AssociationReviewC2S(condition=P.ASSOCIATION_ALL_REVIEW, value=0))
    await call(c, C.MSG_APPLY, P.AssociationApplyC2S(association_ids=[club.id]))
    assert not P.AssociationApplyS2C.FromString(c.last(C.MSG_APPLY)).is_add
    assert [r.friend.gid for r in a.info().reviews] == [9823]
    assert not b.info().reviews  # normal members don't see requests
    await call(a, C.MSG_JOIN, P.AssociationJoinC2S(target_id=[9823], action=True))
    assert c.info().association.id == club.id and len(a.info().numbers) == 3

    # roles: b vice leader, b kicks... only normal members; a hands over to b
    await call(a, C.MSG_APPOINT, P.AssociationAppointC2S(appoint=P.ASSOCIATION_VICE_LEADER, terget_id=9822))
    assert b.info().self_number.position == P.ASSOCIATION_VICE_LEADER
    await call(b, C.MSG_EXIT, P.AssociationExitC2S(target_id=9821))  # vice can't kick the leader
    assert err(b, C.MSG_EXIT, "AssociationExitS2C") != 0

    # notice, welfare, club chat
    await call(a, C.MSG_NOTIFY, P.AssociationNotifyC2S(title="Training", content="Heute 20 Uhr"))
    assert c.info().association_ex.notify[0].content == "Heute 20 Uhr"
    await call(c, C.MSG_DAILY)
    assert err(c, C.MSG_DAILY, "AssociationDailyFreeS2C") == 0
    await call(c, C.MSG_DAILY)
    assert err(c, C.MSG_DAILY, "AssociationDailyFreeS2C") != 0  # once a day
    me = c.info().self_number
    print("after welfare: points", me.gold, "contribution", me.contribution, "club funds", c.info().association.cur_funds)
    assert me.gold == 450 and me.contribution == 50 and c.info().association.cur_funds == 100
    assert c.account.item_count(C.FUNDS) == 0  # club items are not inventory items

    # level up: costs 5000 funds (level 1), leader / vice only
    await call(a, C.MSG_LV)
    assert err(a, C.MSG_LV, "AssociationLvS2C") != 0  # not enough funds
    C.club_of(9821)["funds"] = 5100
    await call(c, C.MSG_LV)
    assert err(c, C.MSG_LV, "AssociationLvS2C") != 0  # normal member
    await call(a, C.MSG_LV)
    org = b.info().association
    print("level up ->", org.lv, "funds", org.cur_funds)
    assert org.lv == 2 and org.cur_funds == 100

    # weekly benefits by last week's contribution (c: 50 -> first tier), then reset
    import seasons, mail
    C.weekly(time.time())  # sets the current week
    C._st()["week"] = "2000-01"
    assert C.weekly(time.time()) == 1
    wk = next(m for m in mail.mail_list(9823) if m.title == mail.TEMPLATES[C.MAIL_WEEK]["Mailtitle"])
    print("weekly mail", [(i.conf_id, i.number) for i in wk.item])
    assert C.club_of(9823)["members"]["9823"]["contrib"] == 0
    await call(c, chat.MSG_SEND, P.ChatMsgSendC2S(chan=P.ASSOCIATION_CHAN, content="hallo club"))
    for x in (a, b, c):
        upd = P.ChatUpdateS2C.FromString(x.last(chat.MSG_UPDATE))
        assert upd.association_msg_list[0].content == "hallo club"
    assert upd.new_chat_person[0].association_name == "Karasuno"

    # wishes and donations: a wishes for shards of player 1103 (rarity 4: max 3), b and c donate
    chip = C.PIECES[1103]
    await call(a, C.MSG_WISH, P.AssociationVowWishC2S(player_id=1103))
    assert err(a, C.MSG_WISH, "AssociationVowWishS2C") == 0
    await call(a, C.MSG_WISH, P.AssociationVowWishC2S(player_id=1101))  # once a day
    assert err(a, C.MSG_WISH, "AssociationVowWishS2C") != 0
    vows = P.AssociationVowInfoS2C.FromString(b.last(C.MSG_VOW_INFO))
    assert [(v.gid, v.player_id, v.get_num) for v in vows.data] == [(9821, 1103, 0)]
    await call(b, C.MSG_DONATE, P.AssociationVowDonateC2S(donee_id=9821, player_id=1103))
    assert err(b, C.MSG_DONATE, "AssociationVowDonateS2C") != 0  # b has no shard
    b.account.set_item(chip, 2)
    c.account.set_item(chip, 2)
    pts = b.info().self_number.gold
    for x in (b, b, c):
        await call(x, C.MSG_DONATE, P.AssociationVowDonateC2S(donee_id=9821, player_id=1103))
        assert err(x, C.MSG_DONATE, "AssociationVowDonateS2C") == 0
    await call(c, C.MSG_DONATE, P.AssociationVowDonateC2S(donee_id=9821, player_id=1103))
    assert err(c, C.MSG_DONATE, "AssociationVowDonateS2C") != 0  # wish full (3)
    assert b.account.item_count(chip) == 0 and b.info().self_number.gold == pts + 600
    rec = P.AssociationVowRecordS2C.FromString(b.last(C.MSG_VOW_RECORD))
    assert len(rec.donate_record) == 2 and rec.extend_data.donate_num == 2
    have = a.account.item_count(chip)
    await call(a, C.MSG_RECEIVE)
    print("wisher shards", have, "->", a.account.item_count(chip))
    assert a.account.item_count(chip) == have + 3
    assert len(P.AssociationVowReceiveS2C.FromString(a.last(C.MSG_RECEIVE)).data.donor_record) == 3
    await call(a, C.MSG_RECEIVE)  # nothing left: no error (sent on every opening of the view), no data
    r = P.AssociationVowReceiveS2C.FromString(a.last(C.MSG_RECEIVE))
    assert r.err == 0 and not r.data.donor_record
    got = P.AssociationVowRecordS2C.FromString(a.last(C.MSG_VOW_RECORD)).donee_record
    await call(a, C.MSG_THANK, P.AssociationVowThankC2S(inst_id=got[0].inst_id))
    assert P.AssociationVowRecordS2C.FromString(b.last(C.MSG_VOW_RECORD)).donate_record[0].is_thank or         P.AssociationVowRecordS2C.FromString(b.last(C.MSG_VOW_RECORD)).donate_record[1].is_thank

    # club boss: a fight's score (own attack + defence power) goes to the club and the player's day best
    boss = P.AssociationBossInfoS2C.FromString(C.boss_info(9821))
    print("boss", boss.common.boss_id, "stage", C.boss_stage(C.club_of(9821)), "index", boss.stage_index)
    assert boss.common.boss_id in C.BOSS_STAGES and C.boss_stage(C.club_of(9821)) > 0

    class Live:
        power_tot = {0: [30000.0, 12000.0], 3: [8000.0, 0.0], 14: [99999.0, 99999.0]}  # 14 = the boss side

    class Fight:
        live, info = Live(), P.PVPRoomInfo()
    assert C.boss_result(9821, Fight()) == 50000
    assert C.boss_result(9822, Fight()) == 50000
    await asyncio.sleep(0.05)
    boss = P.AssociationBossInfoS2C.FromString(a.last(C.MSG_BOSS_INFO))
    print("club score", boss.common.current_score, "my day", boss.info.day_score, "fights", boss.info.day_challenge_num)
    assert boss.common.current_score == 100000 and boss.info.day_score == 50000 and boss.info.day_challenge_num == 1
    gold = a.info().self_number.gold
    await call(a, C.MSG_BOSS_REWARD, P.AssociationBossReceiveRewardC2S(reward_type=1))  # personal tiers <= 50000
    assert err(a, C.MSG_BOSS_REWARD, "AssociationBossReceiveRewardS2C") == 0
    own = list(P.AssociationBossInfoS2C.FromString(a.last(C.MSG_BOSS_INFO)).info.own_receive)
    print("personal tiers claimed", own, "club points", gold, "->", a.info().self_number.gold)
    assert own and a.info().self_number.gold > gold
    await call(a, C.MSG_BOSS_REWARD, P.AssociationBossReceiveRewardC2S(reward_type=1))
    assert err(a, C.MSG_BOSS_REWARD, "AssociationBossReceiveRewardS2C") != 0  # nothing new
    await call(a, C.MSG_BOSS_REWARD, P.AssociationBossReceiveRewardC2S(reward_type=2))  # club tiers <= 100000
    assert list(P.AssociationBossInfoS2C.FromString(a.last(C.MSG_BOSS_INFO)).info.common_receive)

    # the view's member ranking: boss_score = the member's week total; club ranking (rank type 14 / 5)
    assert {x.base.id: x.base.boss_score for x in a.info().numbers}[9821] == 50000
    await call(a, 0x1301, P.RankListC2S(rank_type=P.RANK_ASSOC_BOSS))
    rk = P.RankListS2C.FromString(a.last(0x1301)).rank_data
    print("club ranking", [(r.rank, r.name, r.score) for r in rk.rank], "own", rk.own.rank)
    assert rk.rank[0].name == "Karasuno" and rk.rank[0].score == 100000 and rk.own.rank == 1
    await call(a, 0x1302, P.RankSelfC2S(rank_type=P.RANK_ASSOCIATION_TOTAL_FOUNDS))
    assert P.RankSelfS2C.FromString(a.last(0x1302)).own.gid == C.club_of(9821)["id"]

    # quick finish: the week's best counts again once today's best is below it
    C.club_of(9821)["members"]["9821"]["boss"]["day_score"] = 1000
    q = P.PVPQuickBattleC2S(args=P.PVPRoomArgs(pvp_type=P.FIGHT_ASSOC_BOSS, pvp_args=[0, C.boss_stage(C.club_of(9821))]))
    await call(a, 0x0306, q)
    r = P.PVPQuickBattleS2C.FromString(a.last(0x0306))
    assert r.error == 0 and r.battle_result.info.room_args.pvp_type == P.FIGHT_ASSOC_BOSS
    assert C._boss_club(C.club_of(9821))["score"] == 149000  # only the improvement 1000 -> 50000 counts
    await call(a, 0x0306, q)  # today's best = week's best now: no more quick finish
    assert P.PVPQuickBattleS2C.FromString(a.last(0x0306)).error != 0

    # kick c (leader), c gets the kick message
    await call(a, C.MSG_EXIT, P.AssociationExitC2S(target_id=9823))
    assert P.AssociationExitS2C.FromString(c.last(C.MSG_EXIT)).target_id == 9823
    assert not c.info().HasField("association") and len(a.info().numbers) == 2
    await call(c, C.MSG_APPLY, P.AssociationApplyC2S(association_ids=[club.id]))  # 24 h wait
    assert err(c, C.MSG_APPLY, "AssociationApplyS2C") != 0

    # the leader leaves: b (vice) takes over; b alone leaves: club gone
    await call(a, C.MSG_EXIT, P.AssociationExitC2S(target_id=9821))
    assert P.AssociationExitS2C.FromString(a.last(C.MSG_EXIT)).is_exit
    assert b.info().self_number.position == P.ASSOCIATION_LEADER and len(b.info().numbers) == 1
    b = await login(9822)  # state after a new login
    assert b.info().association.name == "Karasuno"
    await call(b, C.MSG_DISBAND)
    assert b.all(C.MSG_DISBAND) and not b.info().HasField("association")
    assert not C._st()["clubs"]
    print("OK", TMP)


asyncio.run(main())
