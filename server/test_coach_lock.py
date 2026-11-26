"""Offline test of coach lock (0x5504) and coach settings (0x5506) as the client's code needs them: the
change reaches the client through CoachUpdateS2C before the reply and is kept per player. Temporary accounts.
  python test_coach_lock.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-coachlock-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import coach  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


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


async def call(c, handler, pb):
    start = len(c.sent)
    await handler(c, pb)
    return c.sent[start:]


async def main():
    c = await login(9881)
    gid, acc = int(c.gid), c.account
    for n in range(3):  # starter accounts have no coaches
        coach._save(gid, P.CoachCard(inst_id=1152921504606885600 + n, conf_id=18, level=1 + n))
    have = coach.cards(gid, acc)
    check("handlers registered", server.MESSAGE_HANDLERS.get(0x5504) and server.MESSAGE_HANDLERS.get(0x5506))
    check("the account has coaches", len(have) > 2, f"{len(have)} coaches")
    card = next(x for x in sorted(have.values(), key=lambda x: x.inst_id) if not x.locked)
    inst = card.inst_id

    sent = await call(c, coach.handle_lock, P.CoachLockC2S(inst_id=inst, lock=True).SerializeToString())
    order = [m for m, _ in sent]
    upd = P.CoachUpdateS2C.FromString(sent[0][1])
    rep = P.CoachLockS2C.FromString(sent[-1][1])
    check("lock: CoachUpdate with the card, then the reply", order == [coach.MSG_UPDATE, coach.MSG_LOCK], str([hex(m) for m in order]))
    check("lock: the update carries the locked card unchanged otherwise", len(upd.coach_cards) == 1 and upd.coach_cards[0].inst_id == inst and upd.coach_cards[0].locked
          and (upd.coach_cards[0].conf_id, upd.coach_cards[0].level) == (card.conf_id, card.level) and not upd.HasField("setting"))
    check("lock: the reply names the card as locked (prompt text)", rep.err == 0 and rep.coach_card.inst_id == inst and rep.coach_card.locked)
    check("lock: kept", coach.cards(gid, acc)[inst].locked)

    sent = await call(c, coach.handle_decomp, P.CoachDecompC2S(inst_ids=[inst]).SerializeToString())
    check("a locked coach cannot be dismantled", P.CoachDecompS2C.FromString(sent[-1][1]).err != 0 and inst in coach.cards(gid, acc))

    sent = await call(c, coach.handle_lock, P.CoachLockC2S(inst_id=inst, lock=False).SerializeToString())
    rep = P.CoachLockS2C.FromString(sent[-1][1])
    check("unlock: reply and state unlocked", rep.err == 0 and not rep.coach_card.locked and not coach.cards(gid, acc)[inst].locked
          and not P.CoachUpdateS2C.FromString(sent[0][1]).coach_cards[0].locked)

    sent = await call(c, coach.handle_lock, P.CoachLockC2S(inst_id=12345, lock=True).SerializeToString())
    check("unknown coach: error, no update", [m for m, _ in sent] == [coach.MSG_LOCK] and P.CoachLockS2C.FromString(sent[0][1]).err != 0)

    # settings
    before = P.CoachInfoS2C.FromString(coach.info(gid, acc)).setting
    want = P.CoachCardSetting(skip_lv_up_anim=not before.skip_lv_up_anim, auto_resolve_rare=P.COACH_CARD_RARE_BLUE, auto_resolve_confids=[18, 21], onekey_lv_up=1)
    sent = await call(c, coach.handle_setting, P.CoachSettingC2S(setting=want).SerializeToString())
    order = [m for m, _ in sent]
    upd = P.CoachUpdateS2C.FromString(sent[0][1])
    check("setting: CoachUpdate with the setting, then the reply", order == [coach.MSG_UPDATE, coach.MSG_SETTING] and P.CoachSettingS2C.FromString(sent[-1][1]).err == 0)
    check("setting: the update carries exactly the request, no cards", upd.HasField("setting") and upd.setting == want and not upd.coach_cards)
    check("setting: sent again at login", P.CoachInfoS2C.FromString(coach.info(gid, acc)).setting == want)

    sent = await call(c, coach.handle_setting, P.CoachSettingC2S(setting=P.CoachCardSetting()).SerializeToString())
    upd = P.CoachUpdateS2C.FromString(sent[0][1])
    check("setting: everything switched off still reaches the client", upd.HasField("setting") and upd.setting == P.CoachCardSetting()
          and P.CoachInfoS2C.FromString(coach.info(gid, acc)).setting == P.CoachCardSetting())

    d = await login(9882)
    check("another account is not affected", P.CoachInfoS2C.FromString(coach.info(int(d.gid), d.account)).setting == before
          and not coach.cards(int(d.gid), d.account))

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
