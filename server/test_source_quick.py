"""Practice Match quick clear (sourcequick.py, 0x0306 FIGHT_SOURCE) as in the Garena capture dizzy2711h_sweep:
request [line-up, stage, 0, count], 15 stamina per run, one reward_list per run with the stage Reward rolls,
the summary in `rewards` (106 shown as 137), winner 1, star 7; refused without stamina or for a stage without
3 stars. Temporary accounts, no port.
  python test_source_quick.py
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-sourcequick-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402
import sourcequick as Q  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []
STAGE = 230411  # Fukurodani Match 11, as captured


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


async def quick(c, stage, count):
    c.sent = []
    req = P.PVPQuickBattleC2S(args=P.PVPRoomArgs(pvp_type=P.FIGHT_SOURCE, pvp_args=[1, stage, 0, count]))
    await server.MESSAGE_HANDLERS[0x0306](c, req.SerializeToString())
    return c.sent, P.PVPQuickBattleS2C.FromString(c.sent[-1][1])


async def main():
    c = FakeClient(991501)
    await server.handle_login(c, wire.build([(1, 0, 991501), (7, 2, b"token")]))
    acc = c.account
    acc.clear_stage(P.FIGHT_SOURCE, STAGE, 0b111)
    acc.set_currency(105, 240)
    sent, rep = await quick(c, STAGE, 3)
    br = rep.battle_result
    check("3 runs: 15 stamina each (240 -> 195)", acc.get_currency(105) == 195, str(acc.get_currency(105)))
    check("reply without error, winner 1, star 7", rep.error == 0 and br.result.winner == 1 and br.star == 7)
    check("one reward_list per run", len(br.reward_list) == 3)
    per_run = [{i.conf_id: i.number for i in lst.item} for lst in br.reward_list]
    check("every run has the sure rolls (106 x150, 113 x150, 127 x3500)",
          all(r.get(106) == 150 and r.get(113) == 150 and r.get(127) == 3500 for r in per_run), str(per_run[0]))
    summary = {i.conf_id: i.number for i in br.rewards}
    check("summary: manager EXP as 137 x450, 113 x450, 127 x10500",
          summary.get(137) == 450 and summary.get(113) == 450 and summary.get(127) == 10500, str(summary))
    check("room info of the stage (a starter account has no cards in its line-up)",
          br.info.HasField("home") and list(br.info.room_args.pvp_args) == [1, STAGE, 0, 3])
    check("stamina change sent first", sent[0][0] == 0x0405)

    acc.set_currency(105, 20)
    _, rep = await quick(c, STAGE, 3)
    check("not enough stamina: refused, nothing taken", rep.error != 0 and acc.get_currency(105) == 20)
    acc.set_currency(105, 240)
    _, rep = await quick(c, 999999, 1)
    check("unknown / not 3-star stage: refused", rep.error != 0 and acc.get_currency(105) == 240)
    # a roll follows the probabilities: items under 10000 do not always come
    import random
    rng = random.Random(3)
    rolls = [dict(Q.roll(STAGE, rng)) for _ in range(400)]
    share = sum(1 for r in rolls if 205000001 in r) / len(rolls)
    check("a 5 % item comes in about 5 % of the runs", 0.01 < share < 0.10, f"{share:.3f}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
