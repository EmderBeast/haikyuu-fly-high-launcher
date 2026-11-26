"""
Triple Clash (Peak Showdown, FIGHT_PEAK = 15) on a shared server: three teams against another
player's three defence teams, best of three, asynchronous like the arena (pvp.py).

Client flow (hotfix PeakBattle.*):
  0x4A01 PeakInfo           season, rank/score, 4 opponents (PeakPlayer: 3 teams), own defend /
                            attack formations (3 PVPTeams each; the client needs them complete)
  0x4A02 / 0x4A03           save defend / attack formations (PeakFormation: card_gid_list = 13 slots)
  0x4A04 ResetChallengeList re-roll the opponents; 0x4A05 buy attempts (arena3v3 rows 5-7)
  0x4A06 RecordLook         fight log; 0x4A07 week chest (arenaquestlist3v3), 0x4A08 ladder chest
  Challenge: per team index t (0..2) either a live match 0x0302 or a quick battle 0x0306, both
  with pvp_args = [opponent index, t]. After each game the client checks the set scores
  (bo_statistics[0].score) and starts the next team until one side has two wins; the last result
  carries player_grade.home/away.peak_score (old/new) for the result screen.

Score, fitted to the 30 captured records (decoded in the multiplayer notes):
  attack win   ceil(K * (1 - E)), K = 78 - 0.0165 * own score, E = 1 / (1 + 10^((enemy - own) / 400))
  attack loss  -round(K * E / 2)          (both captured losses: -9)
  defence win  +round(K * (1 - E) * 0.27) (3 of 4 captured: +5/+5/+5, one +4)
  defence loss -round(K * E * 0.27)       (not in the capture: mirrored guess)
Sets are played to 5 points (captured records). Rewards per series: arena3v3 rows 23 / 24.
"""
import math
import random
import time

import matches
import pvp
import wire
from accounts import CAPTURED

P = matches.P

MSG_INFO, MSG_SET_DEFEND, MSG_SET_ATTACK, MSG_RESET = 0x4A01, 0x4A02, 0x4A03, 0x4A04
MSG_BUY, MSG_RECORD, MSG_WEEK, MSG_LADDER = 0x4A05, 0x4A06, 0x4A07, 0x4A08
TEAMS = 3
SLOTS = 13
COURT = 7
SET_POINTS = 5
MAX_RECORDS = 30

ARENA3 = {r["ID"]: r for r in pvp._cfg("arena3v3")}
LADDER3 = pvp._cfg("arenaladder3v3")
QUESTS3 = pvp._cfg("arenaquestlist3v3")


def value(row: int, default=0):
    v = ARENA3.get(row, {}).get("Value")
    return default if v is None else v


def _first(v):
    return v[0] if isinstance(v, list) else v


FREE_TIMES = _first(value(4, 3))
BUY_MAX = _first(value(5, 3))
BUY_COST_ITEM = _first(value(6, 108))
BUY_PRICES = value(7, [15, 30, 45])
REFRESH_MAX = _first(value(8, 10))
START_SCORE = _first(value(10, 1000))
MIN_SCORE = _first(value(11, 600))
WIN_REWARD = [tuple(value(23, [950000005, 35]))]
LOSS_REWARD = [tuple(value(24, [950000005, 25]))]
LIST_SIZE = 4
SEASON_END_GAP = 8 * 3600  # captured: season ends 8 h before the next one starts


# --- state ----------------------------------------------------------------------------------------
def season(now=None) -> tuple[int, int, int, int]:
    """(id, start, end, next start); the captured peak season 16 ran alongside arena season 15."""
    sid, start, end = pvp.season(now)
    return sid + 1, start, end + 1 - SEASON_END_GAP, end + 1


def _week(now=None) -> str:
    return time.strftime("%G-%V", time.gmtime(time.time() if now is None else now))


def _captured_info() -> P.PeakInfoS2C:
    return P.PeakInfoS2C.FromString(CAPTURED.pb("0x4A01"))


def entry(gid: int, acc=None) -> dict:
    """Peak ladder entry (created on first use); daily / weekly counters reset."""
    peak = pvp.state().setdefault("peak", {})
    d = peak.get(str(gid))
    if d is None:
        d = {"score": START_SCORE, "max": START_SCORE, "ladder": 0, "records": [], "list": []}
        if acc is not None and acc.b is CAPTURED:  # the owner keeps the captured season state
            info = _captured_info()
            d.update(score=info.rank_data.score or START_SCORE, max=info.max_score or info.rank_data.score,
                     ladder=info.ladder_reward)
        peak[str(gid)] = d
    if d.get("day") != pvp._today():
        d.update(day=pvp._today(), times=0, buys=0, ups=0)
    if d.get("week") != _week():
        d.update(week=_week(), week_times=0, week_reward=0)
    return d


def rank_of(gid: int) -> int:
    order = sorted(pvp.state().get("peak", {}).items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    return next((i + 1 for i, (g, _) in enumerate(order) if g == str(gid)), 0)


# --- formations -----------------------------------------------------------------------------------
def _card_power(acc, inst: int) -> int:
    raw = acc.get_card(inst)
    if not raw:
        return 0
    card = P.PlayerCard.FromString(raw)
    return matches.card_entry(card).power


def default_formations(acc) -> list[list[int]]:
    """Three line-ups of 7: the captured owner's own, else the strongest cards split into three
    (cards are reused when an account has fewer than 21)."""
    if acc.b is CAPTURED:
        info = _captured_info()
        out = [[p.info.inst_id for p in t.pvp_formation.players] for t in info.defend.team]
        if len(out) == TEAMS:
            return out
    insts = [i for i in _all_cards(acc) if acc.get_card(i)]
    insts.sort(key=lambda i: -_card_power(acc, i))
    teams = []
    for t in range(TEAMS):
        pick = [insts[(t * COURT + k) % len(insts)] for k in range(COURT)] if insts else []
        teams.append(pick + [0] * (SLOTS - len(pick)))
    return teams


def _all_cards(acc) -> list[int]:
    seen = []
    for inst in list(acc.b.cards) + [P.PlayerCard.FromString(c).inst_id for c in acc.cards]:
        if inst not in seen and inst not in acc.removed_cards:
            seen.append(inst)
    return seen


def formations(gid: int, acc, kind: str) -> list[list[int]]:
    d = entry(gid, acc)
    if not d.get(kind):
        d[kind] = default_formations(acc)
    return d[kind]


def build_team(acc, insts: list[int], gid: int, name: str, icon=(0, 0)) -> P.PVPTeamInfo:
    team = P.PVPTeamInfo()
    team.CopyFrom(pvp._template().home)
    team.gamer_id, team.name, team.is_robot = gid, name, False
    team.icon, team.icon_frame = icon
    del team.formation.players[:]
    del team.skill_extras[:]
    team.formation.ClearField("coach_card")
    del team.formation.fetter_id_levels[:]
    for inst in (insts + [0] * SLOTS)[:SLOTS]:
        raw = acc.get_card(inst) if inst else None
        if raw:
            team.formation.players.append(matches.card_entry(P.PlayerCard.FromString(raw)))
        else:
            team.formation.players.add()
    team.power = sum(p.power for p in team.formation.players[:COURT])
    import admin  # Admin Mode: per-card power changes (no-op without any)
    admin.apply_power(acc, team)
    team.power = sum(p.power for p in team.formation.players[:COURT])
    return team


def pvp_team(team: P.PVPTeamInfo, owner: int = 0, viewer: int = 0) -> P.PVPTeam:
    out = P.PVPTeam(power=pvp.shown(team.power, owner, viewer))
    out.pvp_formation.CopyFrom(team.formation)
    return out


def peak_formation(teams: list[P.PVPTeamInfo], gid: int = 0) -> P.PeakForMation:
    out = P.PeakForMation()
    for t in teams:
        out.team.append(pvp_team(t, gid))
        out.power.append(pvp.shown(t.power, gid))
    return out


def my_teams(acc, gid: int, name: str, kind: str) -> list[P.PVPTeamInfo]:
    prof = pvp.profile(acc, gid, name)
    return [build_team(acc, insts, gid, name, (prof["icon"], prof["frame"]))
            for insts in formations(gid, acc, kind)]


def snapshot(acc, gid: int, name: str):
    """Store the defence teams other players fight against."""
    teams = my_teams(acc, gid, name, "defend")
    snap = pvp.state()["snapshots"].setdefault(str(gid), pvp.profile(acc, gid, name))
    snap["peak"] = [t.SerializeToString().hex() for t in teams]


def snapshot_teams(gid: int) -> list[P.PVPTeamInfo]:
    snap = pvp.state()["snapshots"].get(str(gid), {})
    return [P.PVPTeamInfo.FromString(bytes.fromhex(h)) for h in snap.get("peak", [])]


# --- opponents ------------------------------------------------------------------------------------
def roll_list(gid: int, acc) -> list[dict]:
    me = entry(gid, acc)
    snaps = pvp.state()["snapshots"]
    others = [int(g) for g, s in snaps.items() if g != str(gid) and s.get("peak")]
    others.sort(key=lambda g: abs(entry(g)["score"] - me["score"]))
    picks = random.sample(others[:LIST_SIZE * 2], min(LIST_SIZE, len(others)))
    out = [{"gid": g} for g in picks]
    row = pvp._robot_row(acc.level)
    own = [t.power for t in my_teams(acc, gid, "", "attack")]
    for i in range(LIST_SIZE - len(out)):
        out.append({"gid": -(i + 1), "robot": row.get("ID", 1), "name": f"CPU {i + 1}",
                    "level": row.get("Level", acc.level),
                    "score": max(MIN_SCORE, me["score"] + random.randint(-60, 60)),
                    "power": [int(p * random.uniform(*pvp.ROBOT_POWER)) for p in own]})
    me["list"] = out
    return out


def opponent_teams(e: dict) -> list[P.PVPTeamInfo]:
    if e["gid"] > 0:
        return snapshot_teams(e["gid"])
    return [pvp.robot_team(e.get("robot", 1), t, e.get("name", "CPU"), e["gid"], e.get("power", [0] * 3)[t])
            for t in range(TEAMS)]


def opponent_score(e: dict) -> int:
    return entry(e["gid"])["score"] if e["gid"] > 0 else e.get("score", START_SCORE)


def _k(score: int) -> float:
    return 78 - 0.0165 * score


def _expected(own: int, enemy: int) -> float:
    return 1 / (1 + 10 ** ((enemy - own) / 400))


def attack_change(own: int, enemy: int, won: bool) -> int:
    e = _expected(own, enemy)
    return math.ceil(_k(own) * (1 - e)) if won else -math.floor(_k(own) * e / 2 + 0.5)


def defence_change(own: int, attacker: int, held: bool) -> int:
    e = _expected(own, attacker)
    return math.floor(_k(own) * (1 - e) * 0.27 + 0.5) if held else -math.floor(_k(own) * e * 0.27 + 0.5)


def peak_player(e: dict, own_score: int, viewer: int = 0) -> P.PeakPlayer:
    teams = opponent_teams(e)
    p = P.PeakPlayer(gid=e["gid"], score=opponent_score(e))
    if e["gid"] > 0:
        snap = pvp.state()["snapshots"].get(str(e["gid"]), {})
        p.name, p.icon, p.icon_frame = snap.get("name", ""), snap.get("icon", 0), snap.get("frame", 0)
        p.level, p.rank = snap.get("level", 1), rank_of(e["gid"])
    else:
        p.name, p.level = e.get("name", "CPU"), e.get("level", 1)
        if teams:
            p.icon, p.icon_frame = teams[0].icon, teams[0].icon_frame
    for t in teams:
        p.team.append(pvp_team(t, e["gid"], viewer))
        p.power.append(pvp.shown(t.power, e["gid"], viewer))
    p.add_score = attack_change(own_score, p.score, True)
    return p


def _stale(gid: int) -> bool:
    lst = entry(gid)["list"]
    shown = {e["gid"] for e in lst}
    free = [g for g, s in pvp.state()["snapshots"].items()
            if int(g) != gid and s.get("peak") and int(g) not in shown]
    return any(e["gid"] < 0 for e in lst) and bool(free)


def info(gid: int, acc, name: str) -> bytes:
    me = entry(gid, acc)
    if not me["list"] or _stale(gid):
        roll_list(gid, acc)
    out = P.PeakInfoS2C()
    sid, start, end, nxt = season()
    out.season.season_id, out.season.start_time, out.season.end_time, out.season.next_start_time = sid, start, end, nxt
    out.rank_data.rank, out.rank_data.score = rank_of(gid), me["score"]
    c = out.challenge
    c.challenge_times, c.buy_times, c.challenge_up_times = me["times"], me["buys"], me["ups"]
    for e in me["list"]:
        c.challenge_list.append(peak_player(e, me["score"], gid))
    out.week_reward, out.week_challenge_times = me["week_reward"], me["week_times"]
    out.ladder_reward, out.max_score, out.rank_score = me["ladder"], me["max"], me["score"]
    out.defend.CopyFrom(peak_formation(my_teams(acc, gid, name, "defend"), gid))
    out.attack.CopyFrom(peak_formation(my_teams(acc, gid, name, "attack"), gid))
    return out.SerializeToString()


# --- series ---------------------------------------------------------------------------------------
class PeakGame:
    """One game of a series (team index t); the Match gets it as .peak."""

    def __init__(self, gid: int, index: int, team: int, e: dict):
        self.gid, self.index, self.team, self.entry = gid, index, team, e


def _series(gid: int) -> dict:
    return entry(gid).setdefault("series", {})


def _room(args, acc, gid: int, name: str):
    """(PVPCreateS2C, PeakGame) for pvp_args [opponent index, team index]."""
    if len(args.pvp_args) < 2:
        return None
    index, t = args.pvp_args[0], args.pvp_args[1]
    me = entry(gid, acc)
    if not 0 <= index < len(me["list"]) or not 0 <= t < TEAMS:
        return None
    e = me["list"][index]
    away = opponent_teams(e)
    home = my_teams(acc, gid, name, "attack")
    if len(away) <= t or len(home) <= t:
        return None
    if t == 0:  # a new series uses up one attempt
        me["times"] += 1
        me["week_times"] += 1
        me["series"] = {"index": index, "gid": e["gid"], "wins": [], "begin": int(time.time())}
    now = int(time.time() * 1000)
    create = matches._captured("0x0302", P.PVPCreateS2C)
    info_ = create.info
    info_.room_args.CopyFrom(args)
    info_.home.CopyFrom(home[t])
    info_.away.CopyFrom(away[t])
    info_.away.is_robot = e["gid"] < 0
    while len(info_.away.formation.players) < SLOTS:
        info_.away.formation.players.add()
    info_.preset_match_id = 0
    info_.bo_type, info_.bo_score, info_.time_end = 1, SET_POINTS, 0
    info_.first_serve_team = False
    info_.create_time, info_.session, info_.seed = now // 1000, now, random.getrandbits(31)
    return create, PeakGame(gid, index, t, e)


def build_room(args_pb: bytes, acc, gid: int, name: str):
    """Live game of a series (0x0302 with FIGHT_PEAK), None for other modes."""
    import livematch
    args = P.PVPRoomArgs.FromString(args_pb)
    if args.pvp_type != P.FIGHT_PEAK:
        return None
    room = _room(args, acc, gid, name)
    if room is None:
        return None
    create, game = room
    info_ = create.info
    pvp.save()
    if livematch.LIBRARY:
        live = livematch.LiveMatch(info_, matches.point_chance(info_.home.power, info_.away.power))
        m = matches.Match(info_, False, 0b001, live)
    else:
        won = random.random() < matches.win_chance(info_.home.power, info_.away.power)
        m = matches.Match(info_, won, 0b001)
    m.peak = game
    return create.SerializeToString(), m


def _quick_score(home_power: int, away_power: int) -> tuple[int, int]:
    """A set to 5 decided point by point from the team powers."""
    chance = matches.point_chance(home_power, away_power)
    h = a = 0
    while h < SET_POINTS and a < SET_POINTS:
        if random.random() < chance:
            h += 1
        else:
            a += 1
    return h, a


def _record(d: dict, kind: int, enemy: dict, own_score: int, change: int, games: list, begin: int):
    d["records"].insert(0, {"type": kind, "enemy": enemy, "own": own_score, "change": change,
                            "games": games, "begin": begin, "end": int(time.time()),
                            "season": season()[0]})
    del d["records"][MAX_RECORDS:]


def _card(gid: int, e: dict | None = None) -> dict:
    if gid > 0:
        snap = pvp.state()["snapshots"].get(str(gid), {})
        return {"gid": gid, "name": snap.get("name", ""), "icon": snap.get("icon", 0), "frame": snap.get("frame", 0),
                "level": snap.get("level", 1), "score": entry(gid)["score"], "rank": rank_of(gid)}
    e = e or {}
    teams = opponent_teams(e) if e else []  # robots: the template team's head icon
    return {"gid": gid, "name": e.get("name", "CPU"), "icon": teams[0].icon if teams else 0,
            "frame": teams[0].icon_frame if teams else 0, "level": e.get("level", 1),
            "score": e.get("score", START_SCORE), "rank": 0}


def finish_game(match, home_score: int, away_score: int) -> tuple[bytes, list, dict | None]:
    """Result of one game; at the end of the series also the ladder update.
    Returns (PVPBattleResult bytes, rewards, settled series or None)."""
    game = match.peak
    gid, me = game.gid, entry(game.gid)
    won = home_score > away_score
    series = me.get("series") or {"index": game.index, "gid": game.entry["gid"], "wins": [],
                                  "begin": int(time.time())}
    series.setdefault("games", [])
    series["wins"].append(won)
    series["games"].append({"home": [p.info.conf_id for p in match.info.home.formation.players],
                            "away": [p.info.conf_id for p in match.info.away.formation.players],
                            "hp": match.info.home.power, "ap": match.info.away.power,
                            "hs": home_score, "as": away_score})
    me["series"] = series
    wins = series["wins"]
    done = wins.count(True) >= 2 or wins.count(False) >= 2 or len(wins) >= TEAMS
    res = P.PVPResultS2C.FromString(matches.build_result(match.info, won, 0b111 if won else 0, [],
                                                                matches.played_of(match)))
    br = res.battle_result
    for side, score in ((br.result.home, home_score), (br.result.away, away_score)):
        if not side.bo_statistics:
            side.bo_statistics.add()
        side.bo_statistics[0].score = score
    br.result.home.bo_win_count, br.result.away.bo_win_count = (1, 0) if won else (0, 1)
    rewards, settled = [], None
    if done:
        series_won = wins.count(True) >= 2
        e = game.entry
        old = (me["score"], opponent_score(e))
        change = attack_change(old[0], old[1], series_won)
        me["score"] = max(MIN_SCORE, me["score"] + change)
        me["max"] = max(me["max"], me["score"])
        defender = _card(e["gid"], e)
        _record(me, P.PCR_ATTACK_SUCCEED if series_won else P.PCR_ATTACK_FAIL, defender, me["score"], change,
                series["games"], series["begin"])
        if e["gid"] > 0:
            d = entry(e["gid"])
            dchange = defence_change(d["score"], old[0], not series_won)
            d["score"] = max(MIN_SCORE, d["score"] + dchange)
            d["max"] = max(d["max"], d["score"])
            _record(d, P.PCR_DEFEND_FAIL if series_won else P.PCR_DEFEND_SUCCEED, _card(gid), d["score"], dchange,
                    [{"home": g["away"], "away": g["home"], "hp": g["ap"], "ap": g["hp"], "hs": g["as"], "as": g["hs"]}
                     for g in series["games"]], series["begin"])
        new = (me["score"], opponent_score(e))
        for side, o, n in ((br.player_grade.home, old[0], new[0]), (br.player_grade.away, old[1], new[1])):
            side.peak_score.old_score, side.peak_score.new_score = o, n
        rewards = list(WIN_REWARD if series_won else LOSS_REWARD)
        settled = {"won": series_won, "change": change}
        me["series"] = {}
        if series_won:
            me["list"] = []  # a fresh opponent list after a win, as in the arena
    pvp.save()
    return br.SerializeToString(), rewards, settled


# --- message handlers -----------------------------------------------------------------------------
def _name(client) -> str:
    return pvp.HOOKS["player_name"](client)


def on_login(client) -> list[tuple[int, bytes]]:
    acc, gid, name = client.account, client.gid, _name(client)
    entry(gid, acc)
    snapshot(acc, gid, name)
    out = info(gid, acc, name)
    pvp.save()
    return [(MSG_INFO, out)]


async def handle_info(client, pb: bytes):
    acc = client.account
    snapshot(acc, client.gid, _name(client))
    out = info(client.gid, acc, _name(client))
    pvp.save()
    await client.send_packet(MSG_INFO, out)


def _save_formations(client, pb: bytes, kind: str) -> list[P.PVPTeamInfo]:
    acc, gid = client.account, client.gid
    req = P.PeakSetDefendFormationC2S.FromString(pb)  # same layout as the attack message
    teams = []
    for pf in req.pf_info[:TEAMS]:
        insts = [i if acc.get_card(i) else 0 for i in pf.card_gid_list][:SLOTS]
        teams.append(insts + [0] * (SLOTS - len(insts)))
    if len(teams) == TEAMS:
        entry(gid, acc)[kind] = teams
    if kind == "defend":
        snapshot(acc, gid, _name(client))
    pvp.save()
    return my_teams(acc, gid, _name(client), kind)


async def handle_set_defend(client, pb: bytes):
    teams = _save_formations(client, pb, "defend")
    out = P.PeakSetDefendFormationS2C()
    for t in teams:
        out.team.append(pvp_team(t, client.gid))
        out.power.append(pvp.shown(t.power, client.gid))
    await client.send_packet(MSG_SET_DEFEND, out.SerializeToString())


async def handle_set_attack(client, pb: bytes):
    teams = _save_formations(client, pb, "attack")
    out = P.PeakSetAttackFormationS2C()
    for t in teams:
        out.team.append(pvp_team(t, client.gid))
        out.power.append(pvp.shown(t.power, client.gid))
    await client.send_packet(MSG_SET_ATTACK, out.SerializeToString())


async def handle_reset(client, pb: bytes):
    me = entry(client.gid, client.account)
    out = P.PeakResetChallengeListS2C()
    if me["ups"] >= REFRESH_MAX:
        out.err = 1
    else:
        me["ups"] += 1
        roll_list(client.gid, client.account)
    full = P.PeakInfoS2C.FromString(info(client.gid, client.account, _name(client)))
    out.challenge_data.CopyFrom(full.challenge)
    pvp.save()
    await client.send_packet(MSG_RESET, out.SerializeToString())


async def handle_buy(client, pb: bytes):
    count = max(1, P.PeakBuyChallengeTimesC2S.FromString(pb).count)
    acc, me = client.account, entry(client.gid, client.account)
    count = min(count, BUY_MAX - me["buys"])
    cost = sum(BUY_PRICES[min(me["buys"] + i, len(BUY_PRICES) - 1)] for i in range(count)) if count > 0 else 0
    if count <= 0 or acc.get_currency(BUY_COST_ITEM) < cost:
        await client.send_packet(MSG_BUY, P.PeakBuyChallengeTimesS2C(err=1, buy_times=me["buys"]).SerializeToString())
        return
    cur = acc.set_currency(BUY_COST_ITEM, acc.get_currency(BUY_COST_ITEM) - cost)
    acc.save()
    me["buys"] += count
    pvp.save()
    await client.send_packet(pvp.MSG_CURRENCY_CHANGE, wire.build([(1, 2, cur)]))
    await client.send_packet(MSG_BUY, P.PeakBuyChallengeTimesS2C(buy_times=me["buys"]).SerializeToString())


def _unilateral(u, conf_ids: list, power: int, score: int, gid: int):
    u.card_id_l_ist.extend((conf_ids + [0] * SLOTS)[:SLOTS])
    u.power, u.score, u.gamer_id = power, score, gid


def records(gid: int) -> bytes:
    out = P.PeakRecordLookS2C()
    for r in entry(gid)["records"]:
        e = r["enemy"]
        rec = out.record.add(season_id=r.get("season", season()[0]), pcr_result=r["type"], enemy_id=e["gid"],
                             enemy_name=e["name"], enemy_icon=e["icon"], enemy_icon_frame=e["frame"],
                             enemy_score=e["score"], enemy_level=e["level"], own_score=r["own"],
                             own_change_score=r["change"], begin_time=r["begin"], end_time=r["end"],
                             enemy_rank=e.get("rank", 0))
        for g in r.get("games", []):
            b = rec.bo3_data.add()
            _unilateral(b.single_a, g["away"], g["ap"], g["as"], e["gid"])
            _unilateral(b.single_b, g["home"], g["hp"], g["hs"], gid)
    return out.SerializeToString()


async def handle_record(client, pb: bytes):
    await client.send_packet(MSG_RECORD, records(client.gid))


async def handle_week(client, pb: bytes):
    """Weekly chest index (arenaquestlist3v3 order) once enough series were played this week."""
    idx = P.PeakGetWeekRewardC2S.FromString(pb).index
    me = entry(client.gid, client.account)
    quest = QUESTS3[idx] if 0 <= idx < len(QUESTS3) else None  # client sends the 0-based list index
    bit = 1 << idx
    if not quest or me["week_reward"] & bit or me["week_times"] < quest.get("Value", 0):
        await client.send_packet(MSG_WEEK, P.PeakGetWeekRewardS2C(err=1, week_reward=me["week_reward"])
                                 .SerializeToString())
        return
    me["week_reward"] |= bit
    pvp.save()
    rewards = [tuple(int(x) for x in s.split(":")) for s in quest.get("TaskItemReward") or []]
    await client.send_packet(MSG_WEEK, P.PeakGetWeekRewardS2C(week_reward=me["week_reward"]).SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, True)


async def handle_ladder(client, pb: bytes):
    """Season ladder chest (arenaladder3v3 ID) once the season's highest score reached it."""
    conf = P.PeakGetLadderRewardC2S.FromString(pb).conf_id
    me = entry(client.gid, client.account)
    row = next((r for r in LADDER3 if r["ID"] == conf), None)
    bit = 1 << conf
    if not row or me["ladder"] & bit or me["max"] < row["Score"][0]:
        await client.send_packet(MSG_LADDER, P.PeakGetLadderRewardS2C(err=1, ladder_reward=me["ladder"])
                                 .SerializeToString())
        return
    me["ladder"] |= bit
    pvp.save()
    rewards = [tuple(int(x) for x in s.split(":")) for s in row.get("Reward") or []]
    await client.send_packet(MSG_LADDER, P.PeakGetLadderRewardS2C(ladder_reward=me["ladder"]).SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, True)


async def handle_quick(client, pb: bytes):
    """0x0306 with FIGHT_PEAK: one game of the series, decided at once."""
    req = P.PVPQuickBattleC2S.FromString(pb)
    acc = client.account
    room = _room(req.args, acc, client.gid, _name(client))
    if room is None:
        await client.send_packet(pvp.MSG_QUICK, P.PVPQuickBattleS2C(error=1).SerializeToString())
        return
    create, game = room
    pvp.calibrate(client.gid, req.home_power, create.info.home.power)
    h, a = _quick_score(create.info.home.power, create.info.away.power)
    m = matches.Match(create.info, h > a, 0)
    m.peak = game
    br, rewards, settled = finish_game(m, h, a)
    out = P.PVPQuickBattleS2C()
    out.battle_result.ParseFromString(br)
    await client.send_packet(pvp.MSG_QUICK, out.SerializeToString())
    if settled:
        await pvp.HOOKS["grant_rewards"](client, rewards, False)
        await client.send_packet(MSG_INFO, info(client.gid, acc, _name(client)))
    return game, h, a, settled


async def handle_result(client, match):
    """0x0304 for a live game of a series (called by server.handle_pvp_result)."""
    live = match.live
    h, a = (live.score[0], live.score[1]) if live else ((SET_POINTS, 0) if match.won else (0, SET_POINTS))
    if h == a:  # skipped before the end: the leader takes the set
        h, a = (SET_POINTS, a) if match.won else (h, SET_POINTS)
    elif max(h, a) < SET_POINTS:
        h, a = (SET_POINTS, a) if h > a else (h, SET_POINTS)
    br, rewards, settled = finish_game(match, h, a)
    res = P.PVPResultS2C()
    res.battle_result.ParseFromString(br)
    await pvp.HOOKS["before_result"](client, res.SerializeToString(), match.info)
    await client.send_packet(0x0304, res.SerializeToString())
    if settled:
        await pvp.HOOKS["grant_rewards"](client, rewards, False)
        await client.send_packet(MSG_INFO, info(client.gid, client.account, _name(client)))
    return h, a, settled


HANDLERS = {
    MSG_INFO: handle_info,
    MSG_SET_DEFEND: handle_set_defend,
    MSG_SET_ATTACK: handle_set_attack,
    MSG_RESET: handle_reset,
    MSG_BUY: handle_buy,
    MSG_RECORD: handle_record,
    MSG_WEEK: handle_week,
    MSG_LADDER: handle_ladder,
}
