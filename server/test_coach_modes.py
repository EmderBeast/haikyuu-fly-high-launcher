"""Offline test: the line-up's coach in Triple Clash (peak.py), Freedom Championship (freedom.py) and the team
competition (teamcomp.py). The client names the coach when it saves a line-up (Garena's 0x4A03: PeakFormation
field 7, 0x4004: TeamFormation.coach_card_inst_id field 12); the saved coach goes into the match team built by
peak.build_team with its stats, skills and power (matches.switch_coach / coach_force), and a coach the player
does not have is dropped. Temporary accounts, no port.
  python test_coach_modes.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

import accounts

sys.stdout.reconfigure(encoding="utf-8")
TMP = Path(tempfile.mkdtemp(prefix="hfh-coachmodes-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import coach  # noqa: E402
import freedom  # noqa: E402
import matches  # noqa: E402
import peak  # noqa: E402
import teamcomp  # noqa: E402
import wire  # noqa: E402

P = matches.P
GID = 9931
FAILED = []


def check(what, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + what + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILED.append(what)


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def main():
    c = FakeClient(GID)
    await server.handle_login(c, wire.build([(1, 0, GID), (7, 2, b"token")]))
    acc = c.account
    ukai = coach.admin_build(GID, acc, {"conf": 9, "level": 15, "buffs": [[1, 4071], [2, 4072], [3, 4078],
                                                                          [4, 4079], [5, 4073]]})
    insts = matches._lineup(acc, P.FIGHT_ARENA)[:7]
    check("the account has a line-up", len([i for i in insts if i]) >= 6, str(insts))

    # peak.build_team directly: with the coach the team holds it, is stronger and carries its power
    plain = peak.build_team(acc, insts, GID, "T")
    withc = peak.build_team(acc, insts, GID, "T", coach=ukai.inst_id)
    check("build_team without a coach has none", not plain.formation.HasField("coach_card"))
    check("build_team with the coach: the coach card",
          withc.formation.coach_card.inst_id == ukai.inst_id and len(withc.formation.coach_card.job_buffs) == 5)
    gain = withc.power - plain.power
    check("team power rises by the stats and coach_force", gain > matches.coach_force(ukai) > 0,
          f"+{gain}, coach_force {matches.coach_force(ukai)}")
    stat = lambda t: sum(a.val + a.extrval for p in t.formation.players[:7] for a in p.attr if a.type < 7)
    check("the players' stats rise", stat(withc) > stat(plain), f"{stat(plain)} -> {stat(withc)}")
    unknown = peak.build_team(acc, insts, GID, "T", coach=4711)
    check("a coach the player does not have: none", not unknown.formation.HasField("coach_card")
          and unknown.power == plain.power)

    # Triple Clash: the client's save (0x4A03) names a coach per team
    req = P.PeakSetAttackFormationC2S()
    for t, cid in enumerate((ukai.inst_id, 0, 4711)):
        pf = req.pf_info.add(card_gid_list=insts + [0] * (peak.SLOTS - len(insts)))
        pf.coach_card_inst_id = cid
    await server.MESSAGE_HANDLERS[peak.MSG_SET_ATTACK](c, req.SerializeToString())
    check("peak: the coaches are stored per team (unknown one dropped)",
          peak.coaches_of(GID, acc, "attack") == [ukai.inst_id, 0, 0], str(peak.coaches_of(GID, acc, "attack")))
    reply = P.PeakSetAttackFormationS2C.FromString(c.sent[-1][1])
    check("peak: the reply shows the coach on team 1 only",
          [t.pvp_formation.coach_card.inst_id for t in reply.team] == [ukai.inst_id, 0, 0])
    teams = peak.my_teams(acc, GID, "T", "attack")
    check("peak: the match team of line-up 1 has the coach and more power",
          teams[0].formation.coach_card.inst_id == ukai.inst_id and teams[0].power > teams[1].power,
          f"{teams[0].power} vs {teams[1].power}")

    # team competition: 0x4004 TeamCompetitionFormationSaveC2S, TeamFormation field 12
    req = P.TeamCompetitionFormationSaveC2S()
    req.formation.add(lineup=1, inst_ids=insts, coach_card_inst_id=ukai.inst_id)
    await server.MESSAGE_HANDLERS[teamcomp.MSG_FORMATION_SAVE](c, req.SerializeToString())
    f = next(x for x in teamcomp.formations(GID, acc) if x["lineup"] == 1)
    check("team competition: the coach is stored", f.get("coach") == ukai.inst_id)
    check("team competition: TeamCompetitionInfoS2C returns it",
          any(x.coach_card_inst_id == ukai.inst_id for x in P.TeamCompetitionInfoS2C.FromString(
              teamcomp.info(GID, acc)).formations))

    # the save sent when a Match Streak starts: module [1, 9] (no coach bit 8), no field 12 - the coach stays
    # (seen on the emulator 27.11.2026: module [1, 9], both line-ups without field 12, after editor saves [329, 8] / [321])
    req = P.TeamCompetitionFormationSaveC2S(module=[1, 9])
    req.formation.add(lineup=1, inst_ids=insts)
    req.formation.add(lineup=2, inst_ids=insts)
    await server.MESSAGE_HANDLERS[teamcomp.MSG_FORMATION_SAVE](c, req.SerializeToString())
    f = next(x for x in teamcomp.formations(GID, acc) if x["lineup"] == 1)
    check("team competition: a save without the coach bit keeps the coach", f.get("coach") == ukai.inst_id)
    req = P.TeamCompetitionFormationSaveC2S(module=[1 | 1 << 8])  # coach removed in the editor
    req.formation.add(lineup=1, inst_ids=insts)
    await server.MESSAGE_HANDLERS[teamcomp.MSG_FORMATION_SAVE](c, req.SerializeToString())
    f = next(x for x in teamcomp.formations(GID, acc) if x["lineup"] == 1)
    check("team competition: the coach bit with no coach removes it", f.get("coach") == 0)

    # Freedom Championship: the whole TeamFormation is stored, team_of passes its coach on
    req = P.FightFreedomPlayerLineupSaveC2S(is_attack=True)
    req.formation.inst_ids.extend(insts)
    req.formation.coach_card_inst_id = ukai.inst_id
    await server.MESSAGE_HANDLERS[freedom.MSG_LINEUP_SAVE](c, req.SerializeToString())
    t = freedom.team_of(acc, GID, "T", "attack")
    check("freedom: the attack team has the coach", t.formation.coach_card.inst_id == ukai.inst_id
          and t.power == withc.power, f"{t.power} vs {withc.power}")

    print("FAILED: " + ", ".join(FAILED) if FAILED else "OK", TMP)
    raise SystemExit(1 if FAILED else 0)


asyncio.run(main())
