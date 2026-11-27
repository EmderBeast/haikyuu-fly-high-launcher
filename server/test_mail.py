"""Offline test of the mailbox (mail.py) and the season jobs (seasons.py): captured mails of the
owner, read / take / remove, daily arena mail, Triple Clash and Freedom season ends, no double
mails after a rerun, push to online players. Temporary accounts, no port.
  python test_mail.py
"""
import asyncio
import time
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-mail-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import mail  # noqa: E402
import seasons  # noqa: E402
import personal  # noqa: E402
import peak  # noqa: E402
import freedom  # noqa: E402
import starter  # noqa: E402
import wire  # noqa: E402

# the test's own owner list: a real owners.json next to the server (main checkout) named the real owner account
starter.OWNERS_FILE = TMP / "owners.json"
P = matches.P


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


def box(c) -> list:
    return list(P.MailInfoS2C.FromString(c.last(mail.MSG_INFO)).mail_list)


async def main():
    owner = next(iter(starter.owner_gids()), None)
    if owner:
        o = await login(owner)
        print("owner mails:", len(box(o)))
        assert len(box(o)) >= 10
        first = box(o)[0]
        await server.MESSAGE_HANDLERS[mail.MSG_READ](o, P.MailReadC2S(inst_id=first.inst_id).SerializeToString())
        # the captured mails were all claimed on Garena already (state DONE): nothing to take twice
        await server.MESSAGE_HANDLERS[mail.MSG_GET](o, P.MailGetItemC2S(inst_id=first.inst_id).SerializeToString())
        assert not P.MailGetItemS2C.FromString(o.last(mail.MSG_GET)).inst_ids
        await server.MESSAGE_HANDLERS[mail.MSG_REMOVE](o, P.MailRemoveC2S(inst_id=first.inst_id).SerializeToString())
        n = len(box(o))
        o = await login(owner)
        assert len(box(o)) == n - 1 and all(m.inst_id != first.inst_id for m in box(o))  # remembered

    a, b = await login(9901), await login(9902)
    assert box(a) == []
    pvp.ladder(9901)["score"], pvp.ladder(9902)["score"] = 1300, 1100

    # daily arena mail at 21:00
    t = time.mktime((2026, 11, 24, 21, 5, 0, 0, 0, -1))
    sent = seasons.run(t)
    print("21:05 run:", sent)
    assert sent["arena_daily"] >= 2
    assert seasons.run(t + 60)["arena_daily"] == 0  # once per day
    await asyncio.sleep(0.05)  # the push runs as a task
    assert any(m == mail.MSG_UPDATE for m, _ in a.sent)  # pushed to the online player
    a = await login(9901)
    m1 = box(a)[0]
    print("arena mail:", m1.title, [(i.conf_id, i.number) for i in m1.item], list(m1.content_args))
    assert m1.item and m1.state == P.MAIL_UNREAD

    # read, take all, remove all
    await server.MESSAGE_HANDLERS[mail.MSG_READ](a, P.MailReadC2S(inst_id=m1.inst_id).SerializeToString())
    coins = a.account.get_currency(112) if a.account.is_currency(112) else a.account.item_count(112)
    await server.MESSAGE_HANDLERS[mail.MSG_GET](a, P.MailGetItemC2S(get_all=True).SerializeToString())
    got = P.MailGetItemS2C.FromString(a.last(mail.MSG_GET))
    after = a.account.get_currency(112) if a.account.is_currency(112) else a.account.item_count(112)
    print("took", list(got.inst_ids), "arena coins", coins, "->", after)
    assert after > coins
    await server.MESSAGE_HANDLERS[mail.MSG_REMOVE](a, P.MailRemoveC2S(remove_all=True).SerializeToString())
    a = await login(9901)
    assert box(a) == []

    # Triple Clash season end
    peak.entry(9901)["score"], peak.entry(9902)["score"] = 1500, 1200
    s1 = peak.season(t)[0]
    later = t + 8 * 86400  # 2 Dec: new Triple Clash season, Freedom 104 still running
    sent = seasons.run(later)
    print("8 days later:", sent, "peak season", s1, "->", peak.season(later)[0])
    a = await login(9901)
    pm = next(m for m in box(a) if m.title == "[LID:126043412]")
    print("peak mail:", list(pm.content_args), [(i.conf_id, i.number) for i in pm.item])
    assert pm.content_args[0] in ("1", "2") and pm.item  # the owner account may rank first
    unl = personal.unlocks(9901, later)
    print("unlocked until season end:", unl)
    assert unl and all(t > later for t in unl.values())
    a = await login(9901)
    upd = P.PersonalUpdateS2C.FromString(a.last(personal.MSG_UPDATE))
    assert {tl.item_id for tl in upd.time_limit} == set(unl) and set(unl) <= set(upd.item_ids)

    # equip the unlocked title / frame; the choice comes back after the next login
    title = next(i for i in unl if i // 1000 == 6)
    await server.MESSAGE_HANDLERS[0x210B](a, P.PersonalTitleSetC2S(conf_id=title).SerializeToString())
    assert P.PersonalTitleSetS2C.FromString(a.last(0x210B)).conf_id == title
    await server.MESSAGE_HANDLERS[0x2108](a, P.PersonalShowPlayerListSetC2S(inst_ids=[11, 12]).SerializeToString())
    a = await login(9901)
    assert P.PersonalTitleSetS2C.FromString(a.last(0x210B)).conf_id == title
    assert list(P.PersonalShowPlayerListSetS2C.FromString(a.last(0x2108)).inst_ids) == [11, 12]
    pvp.state()["unlocks"]["9901"][str(title)] = 1  # expired: the baseline title shows again
    a = await login(9901)
    assert not any(m == 0x210B for m, _ in a.sent)

    # Freedom season 104 closes 2026-12-23
    freedom.entry(9901)
    pvp.state()["freedom"]["9901"].update(season=104, score=1700, history=1700, gifts=[])
    pvp.state()["freedom"]["9902"] = dict(pvp.state()["freedom"].get("9902", {}), season=104, score=1000,
                                          history=1000, gifts=[], records=[])
    sent = seasons.run(time.mktime((2026, 12, 24, 0, 0, 0, 0, 0, -1)))
    print("freedom end:", sent)
    a = await login(9901)
    titles = [m.title for m in box(a)]
    assert "[LID:590531209]" in titles and "[LID:512518106]" in titles  # ranking + missed chests
    assert seasons.run(time.mktime((2026, 12, 24, 0, 5, 0, 0, 0, -1)))["freedom"] == 0
    print("OK", TMP)


asyncio.run(main())
