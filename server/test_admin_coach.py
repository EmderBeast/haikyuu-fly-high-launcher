"""Offline test of the Admin Mode coach builder (admin.py queue entry {"coach": ...}, commands /coach, /coaches,
/coachbuffs; coach.check_build / admin_build): a queued coach is in the player's CoachInfoS2C at the next login
with exactly the chosen job buffs, wrong entries change nothing and are reported, the commands list the tables
and queue the coach, a player without admin rights gets nothing, and the coach named by the line-up goes into the
match team (matches.own_coach) where skills.Battle applies its job buffs. Temporary accounts, no port.
  python test_admin_coach.py
"""
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-admincoach-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
admin.MULTIPLAYER = TMP / "multiplayer.json"
admin.ACCOUNTS, admin.UNLOCKED, admin.ADMINS = TMP, TMP / "admin_unlocked.json", TMP / "admins.json"
admin.QUEUE, admin.DONE, admin.LAST_LOGIN = TMP / "admin_queue", TMP / "admin_done", TMP / "last_login.json"
admin.ALL_QUEUE = admin.QUEUE / "all.json"
import coach  # noqa: E402
import mail  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
GID, OTHER = 9891, 9892
PW = "geheim"
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

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def coaches(c):
    return list(P.CoachInfoS2C.FromString(c.last(coach.MSG_INFO)).coach_cards)


async def gift(c, code):
    await server.MESSAGE_HANDLERS[admin.MSG_GIFT_CODE](c, wire.build([(1, 2, code.encode("utf-8"))]))


def mails(gid):
    return [m["content"] for m in mail._box(gid)["mails"]]


def report(gid):
    return json.loads((admin.DONE / f"{gid}.json").read_text(encoding="utf-8"))["done"]


async def main():
    conf = max(coach.COACHES, key=lambda c: coach.COACHES[c]["CoachRare"])  # a top-rarity coach
    pool = coach.COACHES[conf]["CoachBuffId"]
    low = min(coach.COACHES, key=lambda c: coach.COACHES[c]["CoachRare"])
    foreign = next(b for b in pool if b not in coach.COACHES[low]["CoachBuffId"])
    five = [[j, pool[-1 - i]] for i, j in enumerate((1, 2, 3, 4, 5))]

    # --- the checks against the tables ---
    ok, err = coach.check_build({"conf": conf, "level": 15, "buffs": five})
    check("level 15 with five buffs is allowed", err is None and ok["buffs"] == five and ok["lock"] is True, err or "")
    for what, spec in (("unknown coach", {"conf": 999, "level": 1}),
                       ("level 16", {"conf": conf, "level": 16}),
                       ("level 0", {"conf": conf, "level": -1}),
                       ("six buffs", {"conf": conf, "level": 15, "buffs": five + [five[0]]}),
                       ("a buff at level 2", {"conf": conf, "level": 2, "buffs": five[:1]}),
                       ("two buffs at level 5", {"conf": conf, "level": 5, "buffs": five[:2]}),
                       ("job 6", {"conf": conf, "level": 15, "buffs": [[6, pool[0]]]}),
                       ("unknown buff", {"conf": conf, "level": 15, "buffs": [[1, 1]]}),
                       ("a buff this coach cannot roll", {"conf": low, "level": 15, "buffs": [[1, foreign]]}),
                       ("garbage", {"conf": "x", "buffs": [[1]]})):
        ok, err = coach.check_build(spec)
        check(f"refused: {what}", ok is None and bool(err), err or "")
    check("slots per level 1/3/6/14/15", [coach.buff_slots(n) for n in (1, 3, 6, 14, 15)] == [0, 1, 2, 4, 5])
    # the same job and the same buff may repeat (captured coach: job 5 twice, buff 1014 twice)
    ok, err = coach.check_build({"conf": conf, "level": 6, "buffs": [[5, pool[0]], [5, pool[0]]]})
    check("a job and a buff may repeat", err is None, err or "")

    # --- the queue entry: applied at the next login ---
    admin.CONFIG.write_text(json.dumps({"pw_sha256": hashlib.sha256(PW.encode()).hexdigest()}), encoding="utf-8")
    c = await login(GID)
    check("a new player has no coaches", not coaches(c))
    await gift(c, PW)
    check("password unlocks Admin Mode", admin.is_admin(GID))
    admin.queue(GID, [{"coach": {"conf": conf, "level": 15, "buffs": five}},
                      {"coach": {"conf": conf, "level": 2, "buffs": five}},  # wrong: nothing happens
                      {"coach": [{"conf": low, "level": 3, "buffs": [], "lock": False}]}])
    c = await login(GID)
    have = coaches(c)
    check("two coaches after the login (the wrong entry made none)", len(have) == 2, f"{len(have)}")
    mine = next((k for k in have if k.conf_id == conf), None)
    check("the built coach: level, exp, lock, create time",
          mine is not None and (mine.level, mine.exp, mine.locked) == (15, 0, True) and mine.create_time > 0)
    check("exactly the chosen job buffs, in order",
          mine is not None and [[b.job, b.buff_id] for b in mine.job_buffs] == five)
    other = next((k for k in have if k.conf_id == low), None)
    check("the second coach: level 3, no buffs, not locked",
          other is not None and (other.level, len(other.job_buffs), other.locked) == (3, 0, False))
    check("different instance ids above the base", len({k.inst_id for k in have}) == 2
          and all(k.inst_id > coach.INST_BASE for k in have))
    rep = report(GID)
    check("the report names the coach and the refused entry",
          any("Stufe 15 mit 5 Buff" in r for r in rep) and any("Trainer nicht gebaut" in r for r in rep), str(rep))
    check("the queue is empty", not (admin.QUEUE / f"{GID}.json").exists())

    # still there after another login; the game's own handlers work on it (level-up is refused at the top)
    c = await login(GID)
    check("kept over the next login", len(coaches(c)) == 2)
    await server.MESSAGE_HANDLERS[coach.MSG_LOCK](c, P.CoachLockC2S(inst_id=mine.inst_id, lock=False).SerializeToString())
    check("the lock handler knows the coach", P.CoachLockS2C.FromString(c.last(coach.MSG_LOCK)).err == 0)

    # rebuilding an owned coach ("inst"): same instance, new buffs
    admin.queue(GID, [{"coach": {"inst": mine.inst_id, "level": 6, "buffs": five[:2]}},
                      {"coach": {"inst": 12345, "level": 6}}])
    c = await login(GID)
    have = {k.inst_id: k for k in coaches(c)}
    again = have.get(mine.inst_id)
    check("rebuilt in place", len(have) == 2 and again is not None and again.conf_id == conf and again.level == 6
          and [[b.job, b.buff_id] for b in again.job_buffs] == five[:2])
    check("an unknown instance is reported", any("gibt es bei diesem Spieler nicht" in r for r in report(GID)))

    # --- the commands ---
    before = len(mails(GID))
    await gift(c, "/coaches")
    got = "\n".join(mails(GID)[before:])
    check("/coaches lists every coach with its number", all(f"{i}: " in got for i in coach.COACHES), got[:80])
    before = len(mails(GID))
    name = admin.coach_names()[conf].split()[-2].lower()  # last word of the name
    await gift(c, f"/coachbuffs {name}")
    got = "\n".join(mails(GID)[before:])
    best = admin.coach_buffs(conf)
    check("/coachbuffs lists the best roll of every kind", best and all(f"{b}: " in got for b in best)
          and len(best) == len({coach.BUFFS[b]["RareType"] for b in pool}), f"{len(best)} buffs")
    check("buff texts have their numbers filled in", "{0}" not in got)
    before = len(mails(GID))
    await gift(c, f"/coachbuffs {conf} alle")
    check("/coachbuffs … alle lists every roll", sum(m.count(": ") for m in mails(GID)[before:]) >= len(pool))

    await gift(c, f"/coach {name} 9 ws:{pool[-1]} 3:{pool[-2]} op:{pool[-3]}")
    q = json.loads((admin.QUEUE / f"{GID}.json").read_text(encoding="utf-8"))
    check("/coach queues the checked entry (name = highest rarity)",
          q == [{"coach": {"conf": conf, "level": 9, "buffs": [[1, pool[-1]], [3, pool[-2]], [5, pool[-3]]],
                           "lock": True}}], str(q))
    await gift(c, f"/coach {name} 2 6 ws:{coach.COACHES[low]['CoachBuffId'][0]}")  # "<name> <rarity> <level>"
    q = json.loads((admin.QUEUE / f"{GID}.json").read_text(encoding="utf-8"))
    check("/coach <name> <rarity> <level>", len(q) == 2 and coach.COACHES[q[1]["coach"]["conf"]]["CoachRare"] == 2
          and q[1]["coach"]["level"] == 6, str(q[-1]))
    before = len(mails(GID))
    await gift(c, f"/coach {name} 3 ws:{pool[-1]} mb:{pool[-2]}")  # two buffs at level 3
    await gift(c, f"/coach niemand 15")
    await gift(c, "/coach")
    q = json.loads((admin.QUEUE / f"{GID}.json").read_text(encoding="utf-8"))
    got = mails(GID)[before:]
    check("wrong /coach commands queue nothing and say why", len(q) == 2 and len(got) == 3
          and "Zu viele Buffs" in got[0] and "nicht eindeutig" in got[1] and "Trainer bauen" in got[2], str(got)[:200])
    c = await login(GID)
    built = [k for k in coaches(c) if k.level == 9]
    check("the commanded coach is there after the login", len(coaches(c)) == 4 and len(built) == 1
          and [(b.job, b.buff_id) for b in built[0].job_buffs] == [(1, pool[-1]), (3, pool[-2]), (5, pool[-3])])

    # for another player: /coach ... @gid
    o = await login(OTHER)
    await gift(c, f"/coach {conf} 15 l:{pool[0]} @{OTHER}")
    o = await login(OTHER)
    check("a coach for another player", len(coaches(o)) == 1 and coaches(o)[0].job_buffs[0].job == 4)

    # --- the match: the line-up's coach (TeamFormation.coach_card_inst_id) goes into the match team ---
    acc = c.account
    template = matches._captured("0x0302", P.PVPCreateS2C).info.home
    team = matches.home_team(template, acc, GID, "Test", P.FIGHT_ARENA)
    check("a line-up without a coach: no coach in the match", not team.formation.HasField("coach_card"))
    form = P.TeamFormation()
    form.CopyFrom(matches._formation(acc, P.FIGHT_ARENA))
    form.coach_card_inst_id = built[0].inst_id
    acc.save_formation(form.SerializeToString())
    team = matches.home_team(template, acc, GID, "Test", P.FIGHT_ARENA)
    got = team.formation.coach_card
    check("the line-up's coach is the match team's coach", got.inst_id == built[0].inst_id and got.level == 9
          and [(b.job, b.buff_id) for b in got.job_buffs] == [(1, pool[-1]), (3, pool[-2]), (5, pool[-3])])
    import engine
    import skills
    info = matches._captured("0x0302", P.PVPCreateS2C).info
    info.home.CopyFrom(team)
    battle = skills.Battle(info, engine.stats_for(info))
    want = {b.job: skills._num(skills.COACHBUFF[b.buff_id]["CoachBuffId"]) for b in got.job_buffs}
    hit = miss = 0
    for pid, card in battle.cards.items():
        if battle.team_of(pid) != skills.HOST or battle.jobs.get(pid) not in want:
            continue
        sid = want[battle.jobs[pid]]
        if sid in skills.SKILLS:
            has = any(sk is skills.SKILLS[sid] for sk, _ in battle.skills[pid])
            hit, miss = hit + has, miss + (not has)
    check("skills.Battle gives the coach's job buffs to the players of that job", hit > 0 and miss == 0,
          f"{hit} players, {miss} missing")
    form.coach_card_inst_id = 4711  # a coach the player does not have
    acc.save_formation(form.SerializeToString())
    check("an unknown coach in the line-up: none in the match",
          not matches.home_team(template, acc, GID, "Test", P.FIGHT_ARENA).formation.HasField("coach_card"))

    # shared server: a normal player's command does nothing
    admin.MULTIPLAYER.write_text("{}", encoding="utf-8")
    admin.save_admins({"owner": str(GID), "admins": {}})
    await gift(o, f"/coach {conf} 15 l:{pool[0]}")
    check("no rights on a shared server: nothing queued", not (admin.QUEUE / f"{OTHER}.json").exists())
    print("FAILED: " + ", ".join(FAILED) if FAILED else "OK", TMP)
    raise SystemExit(1 if FAILED else 0)


asyncio.run(main())
