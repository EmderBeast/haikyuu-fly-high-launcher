"""
Freedom Championship (FightFreedom, FIGHT_FIGHTFREEDOM = 18) on a shared server: matchmaking
against the other players' saved Freedom line-ups (robots from freedomrobot fill in), one live
match, a seasonal score ladder.

Client flow (hotfix FreeMatch.*):
  0x5201 FightFreedomInfo   season (id 0 = closed), challenge counters, rank/score, defence and
                            attack formations (TeamFormation), a pending match
  0x5202 Update             season / challenge / rank after a match
  0x520A Match{pattern}     after the "matching" screen (freedomconstantconf 1: 3-5 s);
                            pattern 0 = training match (no score), 1 = score match (uses an attempt)
  0x0302 PVPCreate          FIGHT_FIGHTFREEDOM, pvp_args [0] -> the matched opponent
  0x0305 PVPClose           leaving the start screen gives the match up
  0x5208 LineupSave         {formation, module, is_attack}; 0x5207 an opponent's line-up
  0x5206 ChallengeBuy, 0x5204 Record, 0x5203 Gift (season rank chest, freedomrankconf Reward)
  0x51xx TeamFreedom*       the mode's own line-up slots (stored and echoed)
  Result screen: bo_statistics[0].score and player_grade.home/away.fight_freedom_score (old/new).
The client only opens the mode while a freedomchampionshipconf season is running
(FreeMatchUtils.IsActiveOpen); season 104 runs 2026-11-25 06:00 .. 2026-12-23 05:59.

Score (no captures; assumption from freedomrankconf.WinParam per band, documented in
docs/multiplayer.md): W = WinParam[0] / 500, L = (WinParam[0] - WinParam[1]) / 500,
win +round(2W(1-E)), loss -round(2L*E) with Elo E (/400); start 1000 (constant 12), never below
1000. Defender: a quarter of that, opposite sign.
"""
import asyncio
import os
import random
import time

import matches
import pvp
import wire
from accounts import CAPTURED

P = matches.P

MSG_INFO, MSG_UPDATE, MSG_GIFT, MSG_RECORD = 0x5201, 0x5202, 0x5203, 0x5204
MSG_BUY, MSG_LINEUP, MSG_LINEUP_SAVE, MSG_REWARD, MSG_MATCH = 0x5206, 0x5207, 0x5208, 0x5209, 0x520A
MSG_TEAM_INFO, MSG_TEAM_SAVE, MSG_TEAM_UPDATE, MSG_TEAM_NAME, MSG_TEAM_SEE = 0x5101, 0x5102, 0x5103, 0x5104, 0x5105
MSG_CLOSE = 0x0305
SLOTS, COURT = 13, 7
MAX_RECORDS = 30
TRAIN, SCORE = 0, 1

CONST = {r["ID"]: r for r in pvp._cfg("freedomconstantconf")}
RANKS = pvp._cfg("freedomrankconf")
SEASONS = pvp._cfg("freedomchampionshipconf")
ROBOTS = pvp._cfg("freedomrobot")


def const(row: int, default=0):
    r = CONST.get(row, {})
    v = r.get("Value1")
    if v is None or v == -2147483648:
        v2 = r.get("Value2") or ""
        return [int(x) for x in v2.split(",") if x.strip().lstrip("-").isdigit()] or default
    return v


FREE_TIMES = const(4, 5)
BUY_COST_ITEM = const(5, 108)
BUY_PRICES = const(6, [5] * 5)
START_SCORE = const(12, 1000)


def _time(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%d_%H:%M:%S"))


def current_season(now=None) -> int:
    """Running freedomchampionshipconf season, 0 when closed (HFH_FREEDOM_SEASON forces one)."""
    forced = os.environ.get("HFH_FREEDOM_SEASON")
    if forced:
        return int(forced)
    now = time.time() if now is None else now
    for s in SEASONS:
        if _time(s["OpenTime"]) <= now <= _time(s["CloseTime"]):
            return s["ID"]
    return 0


def band(score: int) -> dict:
    for r in RANKS:
        lo, hi = r["Score"]
        if lo <= score <= hi:
            return r
    return RANKS[-1] if RANKS and score > RANKS[-1]["Score"][0] else (RANKS[0] if RANKS else {})


def _expected(own: int, enemy: int) -> float:
    return 1 / (1 + 10 ** ((enemy - own) / 400))


def score_change(own: int, enemy: int, won: bool) -> int:
    wp = band(own).get("WinParam") or [13000, 1500]
    w, l = wp[0] / 500, (wp[0] - wp[1]) / 500
    e = _expected(own, enemy)
    return round(2 * w * (1 - e)) if won else -round(2 * l * e)


# --- state ----------------------------------------------------------------------------------------
def entry(gid: int, acc=None) -> dict:
    fr = pvp.state().setdefault("freedom", {})
    d = fr.get(str(gid))
    season = current_season()
    if d is None:
        d = fr[str(gid)] = {"season": season, "score": START_SCORE, "history": START_SCORE, "gifts": [],
                            "records": [], "attack": None, "defend": None, "team": None, "match": None}
    if d.get("season") != season and season:  # a new season: fresh ladder, line-ups stay
        d.update(season=season, score=START_SCORE, history=START_SCORE, gifts=[], records=[], match=None)
    if d.get("day") != pvp._today():
        d.update(day=pvp._today(), times=0, buys=0, reward=0)
    return d


def rank_of(gid: int) -> int:
    season = current_season()
    order = sorted(((g, d) for g, d in pvp.state().get("freedom", {}).items() if d.get("season") == season),
                   key=lambda kv: (-kv[1]["score"], kv[0]))
    return next((i + 1 for i, (g, _) in enumerate(order) if g == str(gid)), 0)


def _default_lineup(acc) -> list[int]:
    insts = matches._lineup(acc, P.FIGHT_ARENA)[:SLOTS]
    if not any(insts):
        import peak
        insts = sorted((i for i in peak._all_cards(acc) if acc.get_card(i)),
                       key=lambda i: -peak._card_power(acc, i))[:COURT]
    return (list(insts) + [0] * SLOTS)[:SLOTS]


def formation(gid: int, acc, kind: str) -> P.TeamFormation:
    d = entry(gid, acc)
    if not d.get(kind):
        d[kind] = P.TeamFormation(inst_ids=_default_lineup(acc)).SerializeToString().hex()
    return P.TeamFormation.FromString(bytes.fromhex(d[kind]))


def team_of(acc, gid: int, name: str, kind: str) -> P.PVPTeamInfo:
    import peak
    prof = pvp.profile(acc, gid, name)
    return peak.build_team(acc, list(formation(gid, acc, kind).inst_ids), gid, name, (prof["icon"], prof["frame"]))


def snapshot(acc, gid: int, name: str):
    snap = pvp.state()["snapshots"].setdefault(str(gid), pvp.profile(acc, gid, name))
    snap["freedom"] = team_of(acc, gid, name, "defend").SerializeToString().hex()


def _pvp_team(team: P.PVPTeamInfo, owner: int = 0, viewer: int = 0) -> P.PVPTeam:
    out = P.PVPTeam(power=pvp.shown(team.power, owner, viewer))
    out.pvp_formation.CopyFrom(team.formation)
    return out


# --- matchmaking ----------------------------------------------------------------------------------
def _robot_row(level: int) -> dict:
    return min(ROBOTS, key=lambda r: (abs(r.get("Level", 0) - level), r["ID"])) if ROBOTS else {}


def robot_team(row: dict, name: str, gid: int, power: int) -> P.PVPTeamInfo:
    """A freedomrobot monster team (MonsterTeam list, levels/stars/ratios) scaled to `power`."""
    fake = dict(row, MonsterTeam=[random.choice(row.get("MonsterTeam") or [0])])
    pvp.ARENA_ROBOTS.append(dict(fake, ID=-999))
    try:
        team = pvp.robot_team(-999, 0, name, gid, power)
    finally:
        pvp.ARENA_ROBOTS.pop()
    return team


def find_opponent(gid: int, acc, name: str) -> dict:
    me = entry(gid, acc)
    season = current_season()
    snaps = pvp.state()["snapshots"]
    others = [int(g) for g, s in snaps.items() if g != str(gid) and s.get("freedom")]
    others.sort(key=lambda g: abs(entry(g)["score"] - me["score"]))
    pool = others[:5]
    if pool and random.random() < 0.8:
        g = random.choice(pool)
        return {"gid": g}
    row = _robot_row(acc.level)
    own = team_of(acc, gid, name, "attack").power
    return {"gid": -random.randint(1, 9999), "robot": row.get("ID", 1), "name": "CPU",
            "level": row.get("Level", acc.level), "score": max(START_SCORE, me["score"] + random.randint(-80, 80)),
            "power": int(own * random.uniform(*pvp.ROBOT_POWER)), "season": season}


def opponent_team(e: dict) -> P.PVPTeamInfo | None:
    if e.get("rt_team"):  # real-time partner: the team they attack with
        return P.PVPTeamInfo.FromString(bytes.fromhex(e["rt_team"]))
    if e["gid"] > 0:
        snap = pvp.state()["snapshots"].get(str(e["gid"]), {})
        return P.PVPTeamInfo.FromString(bytes.fromhex(snap["freedom"])) if snap.get("freedom") else None
    row = next((r for r in ROBOTS if r["ID"] == e.get("robot")), _robot_row(e.get("level", 1)))
    random.seed(e["gid"])
    try:
        return robot_team(row, e.get("name", "CPU"), e["gid"], e.get("power", 0))
    finally:
        random.seed()


def opponent_score(e: dict) -> int:
    return entry(e["gid"])["score"] if e["gid"] > 0 else e.get("score", START_SCORE)


def match_msg(gid: int, e: dict, pattern: int, started=False) -> P.FightFreedomMatch:
    team = opponent_team(e)
    m = P.FightFreedomMatch(enemy_id=e["gid"], enemy_score=opponent_score(e), pattern=pattern,
                            score=entry(gid)["score"], started=started)
    if e["gid"] > 0:
        snap = pvp.state()["snapshots"].get(str(e["gid"]), {})
        m.enemy_name, m.enemy_icon, m.enemy_icon_frame = snap.get("name", ""), snap.get("icon", 0), snap.get("frame", 0)
        m.enemy_level = snap.get("level", 1)
    else:
        m.enemy_name, m.enemy_level = e.get("name", "CPU"), e.get("level", 1)
        if team:
            m.enemy_icon, m.enemy_icon_frame = team.icon, team.icon_frame
    if team:
        m.team.CopyFrom(_pvp_team(team, e["gid"], gid))
        m.power = m.team.power
    return m


# --- messages -------------------------------------------------------------------------------------
def season_msg(gid: int) -> P.FightFreedomSeason:
    d = entry(gid)
    s = P.FightFreedomSeason(season_id=current_season())
    s.season_gift_got.extend(d["gifts"])
    s.challenge.CopyFrom(challenge_msg(gid))
    s.rank.CopyFrom(rank_msg(gid))
    return s


def challenge_msg(gid: int) -> P.FightFreedomChallenge:
    d = entry(gid)
    return P.FightFreedomChallenge(challenge_times=d["times"], buy_times=d["buys"],
                                   challenge_reward=d["reward"], challenge_times_max=FREE_TIMES)


def rank_msg(gid: int) -> P.FightFreedomRank:
    d = entry(gid)
    return P.FightFreedomRank(rank=rank_of(gid), score=d["score"], hisotry_score=d["history"])


def info(gid: int, acc, name: str) -> bytes:
    d = entry(gid, acc)
    out = P.FightFreedomInfoS2C()
    out.season.CopyFrom(season_msg(gid))
    out.formations.CopyFrom(formation(gid, acc, "defend"))
    out.attack_formations.CopyFrom(formation(gid, acc, "attack"))
    if d.get("match"):
        out.data.CopyFrom(match_msg(gid, d["match"]["enemy"], d["match"]["pattern"]))
    return out.SerializeToString()


def update(gid: int, acc) -> bytes:
    out = P.FightFreedomUpdateS2C()
    out.season.CopyFrom(season_msg(gid))
    out.challenge.CopyFrom(challenge_msg(gid))
    out.rank.CopyFrom(rank_msg(gid))
    out.attack_formations.CopyFrom(formation(gid, acc, "attack"))
    return out.SerializeToString()


def team_info(gid: int, acc) -> bytes:
    d = entry(gid, acc)
    if d.get("team"):
        return bytes.fromhex(d["team"])
    try:
        return CAPTURED.pb("0x5101") or P.TeamFreedomInfoS2C().SerializeToString()
    except Exception:
        return P.TeamFreedomInfoS2C().SerializeToString()


def _name(client) -> str:
    return pvp.HOOKS["player_name"](client)


def on_login(client) -> list[tuple[int, bytes]]:
    acc, gid, name = client.account, client.gid, _name(client)
    entry(gid, acc)
    snapshot(acc, gid, name)
    out = [(MSG_TEAM_INFO, team_info(gid, acc)), (MSG_INFO, info(gid, acc, name))]
    pvp.save()
    return out


async def handle_match(client, pb: bytes):
    pattern = P.FightFreedomMatchC2S.FromString(pb).pattern
    acc, gid, name = client.account, client.gid, _name(client)
    d = entry(gid, acc)
    out = P.FightFreedomMatchS2C()
    if not current_season():
        out.err = 1
    elif pattern == SCORE and d["times"] >= FREE_TIMES + d["buys"]:
        out.err = 1
    else:
        snapshot(acc, gid, name)
        if realtime_enabled():  # wait for a second searcher without blocking this connection
            asyncio.create_task(_match_realtime(client, pattern))
            return
        e = find_opponent(gid, acc, name)
        d["match"] = {"enemy": e, "pattern": pattern}
        out.data.CopyFrom(match_msg(gid, e, pattern))
    pvp.save()
    await client.send_packet(MSG_MATCH, out.SerializeToString())


def realtime_enabled() -> bool:
    """Real-time pairing on a shared server (multiplayer.json / HFH_MULTIPLAYER) or HFH_REALTIME=1."""
    import admin
    flag = os.environ.get("HFH_REALTIME")
    return flag not in (None, "", "0") if flag is not None else admin.multiplayer()


async def _match_realtime(client, pattern: int):
    """Pair with another searcher (realtime.pair); otherwise a saved opponent as usual."""
    import realtime
    gid, acc, name = client.gid, client.account, _name(client)
    out = P.FightFreedomMatchS2C()
    try:
        found = await realtime.pair(client, pattern)
        d = entry(gid, acc)
        if found:
            key, other, host_gid = found
            other_name = _name(other)
            mine = team_of(acc, gid, name, "attack")
            theirs = team_of(other.account, other.gid, other_name, "attack")
            if host_gid != gid:  # we arrived second: build the shared room now (host = the waiting one)
                realtime.create_room(key, theirs, mine, (other.gid, gid))
            prof = pvp.profile(other.account, other.gid, other_name)
            e = {"gid": other.gid, "rt": key, "rt_team": theirs.SerializeToString().hex(),
                 "name": other_name, "level": prof["level"]}
            d["match"] = {"enemy": e, "pattern": pattern, "rt": key}
        else:
            e = find_opponent(gid, acc, name)
            d["match"] = {"enemy": e, "pattern": pattern}
        out.data.CopyFrom(match_msg(gid, e, pattern))
        pvp.save()
    except Exception:  # never leave the matching screen hanging
        out = P.FightFreedomMatchS2C(err=1)
    await client.send_packet(MSG_MATCH, out.SerializeToString())


async def handle_close(client, pb: bytes):
    """0x0305 PVPClose: leaving the start screen gives the matched opponent up; any other close
    (quitting a running match) is answered as before."""
    d = pvp.state().get("freedom", {}).get(str(client.gid))
    if getattr(client, "room", None) is None and d and d.get("match"):
        d["match"] = None
        pvp.save()
        await client.send_packet(MSG_CLOSE, P.PVPCloseS2C().SerializeToString())
        return
    await pvp.HOOKS["generic"](client, pb, MSG_CLOSE)


class FreedomGame:
    def __init__(self, gid: int, e: dict, pattern: int):
        self.gid, self.entry, self.pattern, self.begin = gid, e, pattern, int(time.time())


def build_room(args_pb: bytes, acc, gid: int, name: str):
    import livematch
    args = P.PVPRoomArgs.FromString(args_pb)
    if args.pvp_type != P.FIGHT_FIGHTFREEDOM:
        return None
    d = entry(gid, acc)
    m = d.get("match")
    if not m:
        return None
    if m.get("rt"):  # real-time match with another player
        import realtime
        joined = realtime.join(m["rt"], gid)
        if joined:
            if m["pattern"] == SCORE:
                d["times"] += 1
            pvp.save()
            create, view = joined
            match = matches.Match(view.info, False, 0b001, view)
            match.freedom = FreedomGame(gid, m["enemy"], m["pattern"])
            return create, match
    away = opponent_team(m["enemy"])
    if away is None:
        return None
    if m["pattern"] == SCORE:
        d["times"] += 1
    now = int(time.time() * 1000)
    create = matches._captured("0x0302", P.PVPCreateS2C)
    info_ = create.info
    info_.room_args.CopyFrom(args)
    info_.home.CopyFrom(team_of(acc, gid, name, "attack"))
    info_.away.CopyFrom(away)
    info_.away.is_robot = m["enemy"]["gid"] < 0
    while len(info_.away.formation.players) < SLOTS:
        info_.away.formation.players.add()
    info_.preset_match_id = 0
    info_.bo_type, info_.bo_score, info_.time_end = 1, matches.LIVE_POINTS, 0
    info_.first_serve_team = False
    info_.create_time, info_.session, info_.seed = now // 1000, now, random.getrandbits(31)
    pvp.save()
    if livematch.LIBRARY:
        live = livematch.LiveMatch(info_, matches.point_chance(info_.home.power, info_.away.power))
        match = matches.Match(info_, False, 0b001, live)
    else:
        match = matches.Match(info_, random.random() < matches.win_chance(info_.home.power, info_.away.power), 0b001)
    match.freedom = FreedomGame(gid, m["enemy"], m["pattern"])
    return create.SerializeToString(), match


def _card(gid: int, e: dict | None = None) -> dict:
    if gid > 0:
        snap = pvp.state()["snapshots"].get(str(gid), {})
        return {"gid": gid, "name": snap.get("name", ""), "icon": snap.get("icon", 0), "frame": snap.get("frame", 0),
                "level": snap.get("level", 1), "score": entry(gid)["score"]}
    e = e or {}
    return {"gid": gid, "name": e.get("name", "CPU"), "icon": 0, "frame": 0, "level": e.get("level", 1),
            "score": e.get("score", START_SCORE)}


def _record(d: dict, kind: int, enemy: dict, own: int, change: int, wins: tuple, begin: int):
    d["records"].insert(0, {"type": kind, "enemy": enemy, "own": own, "change": change, "wins": list(wins),
                            "begin": begin, "end": int(time.time())})
    del d["records"][MAX_RECORDS:]


async def handle_result(client, match):
    game = match.freedom
    gid, d = game.gid, entry(game.gid)
    live = match.live
    bo = match.info.bo_score or matches.LIVE_POINTS
    h, a = (live.score[0], live.score[1]) if live else ((bo, 0) if match.won else (0, bo))
    if live and not live.over:
        h, a = (bo, a) if match.won else (h, max(bo, h + 1))
    won = h > a
    e = game.entry
    res = P.PVPResultS2C.FromString(matches.build_result(match.info, won, 0b111 if won else 0, []))
    br = res.battle_result
    for side, score in ((br.result.home, h), (br.result.away, a)):
        if not side.bo_statistics:
            side.bo_statistics.add()
        side.bo_statistics[0].score = score
    change = 0
    if game.pattern == SCORE:
        old = (d["score"], opponent_score(e))
        d["score"] = max(START_SCORE, d["score"] + score_change(old[0], old[1], won))
        change = d["score"] - old[0]  # the floor at the start score can swallow a loss
        d["history"] = max(d["history"], d["score"])
        _record(d, P.FIGHTFREEDOM_CHALLENGE_WIN if won else P.FIGHTFREEDOM_CHALLENGE_LOSE, _card(e["gid"], e),
                d["score"], change, (1 if won else 0, 0 if won else 1), game.begin)
        if e["gid"] > 0 and not e.get("rt"):  # real-time partners update their own ladder
            od = entry(e["gid"])
            before = od["score"]
            od["score"] = max(START_SCORE, od["score"] + round(score_change(od["score"], old[0], not won) / 4))
            dchange = od["score"] - before
            _record(od, P.FIGHTFREEDOM_DEFENSE_LOSE if won else P.FIGHTFREEDOM_DEFENSE_WIN, _card(gid), od["score"],
                    dchange, (0 if won else 1, 1 if won else 0), game.begin)
        new = (d["score"], opponent_score(e))
        for side, o, n in ((br.player_grade.home, old[0], new[0]), (br.player_grade.away, old[1], new[1])):
            side.fight_freedom_score.old_score, side.fight_freedom_score.new_score = o, n
    d["match"] = None
    pvp.save()
    await client.send_packet(0x0304, res.SerializeToString())
    await client.send_packet(MSG_UPDATE, update(gid, client.account))
    return h, a, change


def on_disconnect(client):
    """A player who leaves a running real-time match loses it (points and record as a lost
    challenge); the other player's match goes on to the end and is counted as usual."""
    match = getattr(client, "room", None)
    game = getattr(match, "freedom", None)
    live = getattr(match, "live", None)
    if game is None or live is None or live.over or not game.entry.get("rt"):
        return
    live.gone = True  # the other player no longer waits for this one (shared tempo)
    if getattr(live, "room", None) is not None:
        live.room.moved.set()
    d = entry(game.gid)
    if not d.get("match"):
        return
    e = game.entry
    if game.pattern == SCORE:
        old = d["score"]
        d["score"] = max(START_SCORE, d["score"] + score_change(old, opponent_score(e), False))
        _record(d, P.FIGHTFREEDOM_CHALLENGE_LOSE, _card(e["gid"], e), d["score"], d["score"] - old, (0, 1),
                game.begin)
    d["match"] = None
    pvp.save()


async def handle_lineup_save(client, pb: bytes):
    acc, gid = client.account, client.gid
    req = P.FightFreedomPlayerLineupSaveC2S.FromString(pb)
    f = P.TeamFormation()
    f.CopyFrom(req.formation)
    ids = [i if acc.get_card(i) else 0 for i in f.inst_ids]
    del f.inst_ids[:]
    f.inst_ids.extend(ids)
    entry(gid, acc)["attack" if req.is_attack else "defend"] = f.SerializeToString().hex()
    if not req.is_attack:
        snapshot(acc, gid, _name(client))
    pvp.save()
    out = P.FightFreedomPlayerLineupSaveS2C(module=req.module, is_attack=req.is_attack)
    out.formation.CopyFrom(f)
    await client.send_packet(MSG_LINEUP_SAVE, out.SerializeToString())


async def handle_lineup(client, pb: bytes):
    see = P.FightFreedomPlayerLineupC2S.FromString(pb).see_gid
    d = entry(client.gid, client.account)
    e = (d.get("match") or {}).get("enemy") if (d.get("match") or {}).get("enemy", {}).get("gid") == see else {"gid": see}
    team = opponent_team(e) if see else None
    out = P.FightFreedomPlayerLineupS2C()
    if team:
        out.team.CopyFrom(_pvp_team(team, see, client.gid))
        out.power = out.team.power
    else:
        out.err = 1
    await client.send_packet(MSG_LINEUP, out.SerializeToString())


async def handle_buy(client, pb: bytes):
    num = max(1, P.FightFreedomChallengeBuyC2S.FromString(pb).num)
    acc, d = client.account, entry(client.gid, client.account)
    num = min(num, len(BUY_PRICES) - d["buys"])
    cost = sum(BUY_PRICES[d["buys"] + i] for i in range(num)) if num > 0 else 0
    if num <= 0 or acc.get_currency(BUY_COST_ITEM) < cost:
        await client.send_packet(MSG_BUY, P.FightFreedomChallengeBuyS2C(err=1, buy_times=d["buys"]).SerializeToString())
        return
    cur = acc.set_currency(BUY_COST_ITEM, acc.get_currency(BUY_COST_ITEM) - cost)
    acc.save()
    d["buys"] += num
    pvp.save()
    await client.send_packet(pvp.MSG_CURRENCY_CHANGE, wire.build([(1, 2, cur)]))
    await client.send_packet(MSG_BUY, P.FightFreedomChallengeBuyS2C(buy_times=d["buys"]).SerializeToString())


async def handle_record(client, pb: bytes):
    out = P.FightFreedomRecordS2C()
    for r in entry(client.gid, client.account)["records"]:
        e = r["enemy"]
        out.records.add(own_gid=client.gid, record_type=r["type"], enemy_id=e["gid"], enemy_name=e["name"],
                        enemy_icon=e["icon"], enemy_icon_frame=e["frame"], enemy_score=e["score"],
                        own_score=r["own"], own_change_score=r["change"], record_id=-1, begin_time=r["begin"],
                        end_time=r["end"], enemy_level=e["level"], own_bo_win_count=r["wins"][0],
                        enem_bo_win_count=r["wins"][1])
    await client.send_packet(MSG_RECORD, out.SerializeToString())


async def handle_gift(client, pb: bytes):
    """Season rank chest: gift_index = freedomrankconf ID with a Reward, reached by the best score."""
    idx = P.FightFreedomGiftC2S.FromString(pb).gift_index
    d = entry(client.gid, client.account)
    row = next((r for r in RANKS if r["ID"] == idx and r.get("Reward")), None)
    if not row or idx in d["gifts"] or d["history"] < row["Score"][0]:
        await client.send_packet(MSG_GIFT, P.FightFreedomGiftS2C(err=1).SerializeToString())
        return
    d["gifts"].append(idx)
    pvp.save()
    import teamcomp
    out = P.FightFreedomGiftS2C()
    out.season_gift_got.extend(d["gifts"])
    await client.send_packet(MSG_GIFT, out.SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, teamcomp.kv(row["Reward"]), True)


DAILY_CHESTS = pvp._cfg("freedailytaskconf")  # per season: chests after N score matches a day


def daily_chests(season: int) -> list[dict]:
    return sorted((r for r in DAILY_CHESTS if r.get("FreedomId") == season), key=lambda r: r["Value"])


async def handle_reward(client, pb: bytes):
    """0x5209 FightFreedomRewardC2S{index}: daily match chest (freedailytaskconf). index is the chest's
    position in the season's list and challenge_reward a bit mask over those positions
    (FreeMatchMainViewLogic.OnListRewardsEvent / UpdateRewards -> UIGenericUtils.IsRewarded(mask, i);
    checked in the emulator)."""
    idx = P.FightFreedomRewardC2S.FromString(pb).index
    d = entry(client.gid, client.account)
    rows = daily_chests(current_season())
    row = rows[idx] if 0 <= idx < len(rows) else None
    if not row or d["reward"] & (1 << idx) or d["times"] < row["Value"]:
        await client.send_packet(MSG_REWARD, P.FightFreedomRewardS2C(err=1, challenge_reward=d["reward"])
                                 .SerializeToString())
        return
    d["reward"] |= 1 << idx
    pvp.save()
    rewards = [tuple(int(x) for x in s.split(":")) for s in row.get("TaskItemReward") or []]
    await client.send_packet(MSG_REWARD, P.FightFreedomRewardS2C(challenge_reward=d["reward"]).SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, True)


async def handle_team_save(client, pb: bytes):
    acc, gid = client.account, client.gid
    req = P.TeamFreedomFormationSaveC2S.FromString(pb)
    current = P.TeamFreedomInfoS2C.FromString(team_info(gid, acc))
    forms = {f.lineup: f for f in current.formations}
    forms[req.formation.lineup] = req.formation
    del current.formations[:]
    current.formations.extend(forms[k] for k in sorted(forms))
    entry(gid, acc)["team"] = current.SerializeToString().hex()
    pvp.save()
    await client.send_packet(MSG_TEAM_SAVE, P.TeamFreedomFormationSaveS2C(module=req.module).SerializeToString())


async def handle_team_see(client, pb: bytes):
    see = P.TeamFreedomSeeOtherC2S.FromString(pb).see_gid
    team = opponent_team({"gid": see}) if see > 0 else None
    out = P.TeamFreedomSeeOtherS2C()
    if team:
        out.team.CopyFrom(_pvp_team(team, see, client.gid))
        out.power = out.team.power
    else:
        out.err = 1
    await client.send_packet(MSG_TEAM_SEE, out.SerializeToString())


HANDLERS = {
    MSG_MATCH: handle_match,
    MSG_LINEUP_SAVE: handle_lineup_save,
    MSG_LINEUP: handle_lineup,
    MSG_BUY: handle_buy,
    MSG_RECORD: handle_record,
    MSG_GIFT: handle_gift,
    MSG_REWARD: handle_reward,
    MSG_TEAM_SAVE: handle_team_save,
    MSG_TEAM_SEE: handle_team_see,
    MSG_CLOSE: handle_close,
}
