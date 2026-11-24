"""
Asynchronous PvP on a shared server: Arena (FIGHT_ARENA = 2).

Opponents are the saved line-ups of the other players on this server (a snapshot of each player's
arena team, taken at login and whenever they open the arena), played by our match engine like a
PvE away team. With fewer than four other players the list is filled with the game's own arena
robots (arenarobot), as Garena's server did.

Client flow (hotfix Arena.* / IL dump):
  0x1401 FightArenaInfo   season, own rank/score, challenge list (4 = arena row 14), counters
  0x1402 ChallengeUp      re-roll the list (arena row 6 per day); we answer with a fresh 0x1401
  0x1405 ChallengeBuy     buy extra attempts (row 3 max, row 5 diamond prices)
  0x1404 Record           fight log; 0x1406 PlayerLineup: an opponent's team (rank list detail)
  0x1403 Gift             season chests by highest score (arenaladder Reward)
  0x1301/0x1302           rank list / own rank, RANK_ARENA (other rank types: captured replies)
  0x0302 PVPCreate        pvp_args = [line-up index, challenge list index] -> live match
  0x0306 PVPQuickBattle   when the own team is >= 15 % stronger (row 23): result right away

Score (fitted to the 30 captured fight records, docs/multiplayer.md): attack win
ceil(K1 * (1 - E)), E = 1 / (1 + 10^((enemy - own) / 350)), K1 from arenaparm by own score;
attack loss 0; defense loss -K4; defense win 0.
"""
import json
import os
import math
import random
import time

import gameconfig
import matches
import wire
from accounts import ACCOUNTS, CAPTURED

P = matches.P
STORE = ACCOUNTS / "pvp.json"

MSG_ARENA_INFO, MSG_ARENA_UP, MSG_ARENA_GIFT, MSG_ARENA_RECORD = 0x1401, 0x1402, 0x1403, 0x1404
MSG_ARENA_BUY, MSG_ARENA_LINEUP = 0x1405, 0x1406
MSG_RANK_LIST, MSG_RANK_SELF = 0x1301, 0x1302
MSG_QUICK = 0x0306
MSG_CURRENCY_CHANGE = 0x0405
DIAMONDS = 108

SEASON_ANCHOR = (15, 1793512800)  # captured season 15 started here; seasons are 30 days
SEASON_LEN = 30 * 86400
MAX_RECORDS = 30
RANK_PAGE = 100


def _cfg(name: str) -> list:
    try:
        return json.loads((matches._CFG / f"{name}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


ARENA = {r["ID"]: r for r in _cfg("arena")}
ARENA_PARM = _cfg("arenaparm")
ARENA_LADDER = _cfg("arenaladder")
ARENA_ROBOTS = _cfg("arenarobot")


def arena_value(row: int, default=0):
    v = ARENA.get(row, {}).get("Value")
    return default if v is None else v


def arena_data(row: int) -> str:
    return ARENA.get(row, {}).get("Data") or ""


FREE_TIMES = arena_value(2, 5)
BUY_MAX = arena_value(3, 5)
BUY_PRICES = arena_value(5, [20, 20, 40, 40, 60])
REFRESH_MAX = arena_value(6, 10)
START_SCORE = arena_value(8, 1000)
LIST_SIZE = arena_value(14, 4)
QUICK_MARGIN = arena_value(23, 1500)  # quick battle if home * 10000 > away * (10000 + margin)


def _items(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in (spec or "").split(","):
        if ":" in part:
            a, b = part.split(":")
            out.append((int(a), int(b)))
    return out


WIN_REWARD = _items(arena_data(20)) or [(112, 20)]
LOSS_REWARD = _items(arena_data(21)) or [(112, 15)]

# robot strength relative to the own team; HFH_ROBOT_POWER="1.4,1.6" for testing live matches
ROBOT_POWER = tuple(float(x) for x in os.environ.get("HFH_ROBOT_POWER", "0.85,1.05").split(","))

# --- persistent state ---------------------------------------------------------------------------
_state = None


def state() -> dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(STORE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _state = {}
        _state.setdefault("snapshots", {})
        _state.setdefault("arena", {})
    return _state


def save():
    ACCOUNTS.mkdir(exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STORE)


def reset_state():  # tests
    global _state
    _state = None


def season(now: float | None = None) -> tuple[int, int, int]:
    """(season id, start, end) of the running 30-day season."""
    now = time.time() if now is None else now
    sid, start = SEASON_ANCHOR
    n = math.floor((now - start) / SEASON_LEN)
    return sid + n, start + n * SEASON_LEN, start + (n + 1) * SEASON_LEN - 1


def _today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


# --- players ------------------------------------------------------------------------------------
def _personal(acc) -> list:
    return wire.parse(acc.b.pb("0x2101"))


def profile(acc, gid: int, name: str) -> dict:
    f = _personal(acc)
    return {"name": name, "icon": wire.get(f, 4, 0), "frame": wire.get(f, 5, 0), "level": acc.level}


def _template() -> P.PVPRoomInfo:
    return matches._captured("0x0302", P.PVPCreateS2C).info


def snapshot(acc, gid: int, name: str) -> dict:
    """Store this player's arena team (what others will play against) and profile."""
    prof = profile(acc, gid, name)
    team = matches.home_team(_template().home, acc, gid, name, P.FIGHT_ARENA)
    team.icon, team.icon_frame = prof["icon"], prof["frame"]
    team.is_robot = False
    snap = dict(prof, team=team.SerializeToString().hex(), power=team.power, time=int(time.time()))
    state()["snapshots"][str(gid)] = snap
    return snap


def snapshot_team(gid: int) -> P.PVPTeamInfo | None:
    snap = state()["snapshots"].get(str(gid))
    if not snap:
        return None
    return P.PVPTeamInfo.FromString(bytes.fromhex(snap["team"]))


def _initial(acc) -> dict:
    """New ladder entry; the captured owner account keeps its real season score."""
    d = {"score": START_SCORE, "history": START_SCORE, "gifts": [], "records": []}
    if acc is not None and acc.b is CAPTURED:
        try:  # the login sync has only season/gift; the full info is the captured 0x1401 reply
            info = matches._captured("0x1401", P.FightArenaInfoS2C)
            d["score"] = info.rank_data.score or START_SCORE
            d["history"] = max(d["score"], info.gift.hisotry_score)
            d["gifts"] = list(info.gift.season_gift_got)
        except Exception:
            pass
    return d


def ladder(gid: int, acc=None) -> dict:
    """Arena entry of a player (created on first use), daily counters reset."""
    arena = state()["arena"]
    d = arena.get(str(gid))
    if d is None:
        d = arena[str(gid)] = _initial(acc)
    if d.get("day") != _today():
        d.update(day=_today(), times=0, buys=0, ups=0)
    d.setdefault("list", [])
    return d


def rank_of(gid: int) -> int:
    """1-based place among all players with an arena entry (robots are not ranked)."""
    order = sorted(state()["arena"].items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    for i, (g, _) in enumerate(order):
        if g == str(gid):
            return i + 1
    return 0


def grade(score: int) -> dict:
    for row in ARENA_LADDER:
        lo, hi = row["Score"]
        if lo <= score <= hi:
            return row
    return ARENA_LADDER[-1] if ARENA_LADDER else {}


# --- shown power ---------------------------------------------------------------------------------
# The server's power (sum of the 7 base stats / 10000) drives robot scaling and win chances. The game
# shows its own figure (TeamPlayerCalculator.CalcPower: skills, specialties, collections ...), 2-3x
# higher, which the server cannot fully rebuild. PVPQuickBattleC2S.home_power is that figure for the
# own team, so its ratio to the server power is remembered per player and applied to shown values.
RATIO_RANGE = (0.5, 10.0)


def _ratios() -> dict:
    return state().setdefault("power_ratio", {})


def calibrate(gid: int, client_power: int, strength: int):
    if gid > 0 and client_power > 0 and strength > 0:
        r = client_power / strength
        if RATIO_RANGE[0] <= r <= RATIO_RANGE[1]:
            _ratios()[str(gid)] = round(r, 4)
            save()


def shown(power: int, owner: int = 0, viewer: int = 0) -> int:
    """Power as the game would show it: the owner's ratio, else the viewer's (robots look as
    strong to the viewer as they really are relative to the own team)."""
    r = (_ratios().get(str(owner)) if owner > 0 else None) or _ratios().get(str(viewer)) or 1.0
    return int(power * r)


# --- robots -------------------------------------------------------------------------------------
def _robot_row(level: int) -> dict:
    if not ARENA_ROBOTS:
        return {}
    return min(ARENA_ROBOTS, key=lambda r: (abs(r.get("Level", 0) - level), r["ID"]))


def robot_team(robot_id: int, team_no: int, name: str, gid: int = 0, power: int = 0) -> P.PVPTeamInfo:
    """Arena robot: one of the row's monster teams with the row's levels/stars/ratios, its stats
    scaled to `power` (the robot tables start at level-35 cards, far above a new account)."""
    row = next((r for r in ARENA_ROBOTS if r["ID"] == robot_id), {})
    tmpl = _template().away
    team = P.PVPTeamInfo()
    team.CopyFrom(tmpl)
    del team.formation.players[:]
    del team.skill_extras[:]
    team.gamer_id = gid or -robot_id
    team.name = name
    team.is_robot = True
    mt = matches.MONSTER_TEAMS.get((row.get("MonsterTeam") or [0])[team_no % max(1, len(row.get("MonsterTeam") or [1]))], {})
    confs = (mt.get("MonsterID") or []) + (mt.get("SubstitutMonsterID") or [])
    levels = (row.get("MonsterLevel") or []) + (row.get("SubstitutMonsterLevel") or [])
    stars = (row.get("MonsterStar") or []) + (row.get("SubstitutMonsterStar") or [])
    now = int(time.time() * 1000)
    for i, conf in enumerate(confs[:matches.TEAM_SIZE]):
        fields = gameconfig.new_card_fields(conf)
        card = P.PlayerCard(inst_id=robot_id * 100000 + team_no * 100 + i + 1, create_time=now // 1000,
                            conf_id=conf, level=levels[i] if i < len(levels) else 1,
                            star=stars[i] if i < len(stars) else 3, skills=fields[1] if fields else [])
        team.formation.players.append(matches.card_entry(card, row))
    del team.tactics[:]
    attack, defense = row.get("AttackTactics") or [1001, 1], row.get("DefenseTactics") or [2003, 1]
    team.tactics.add(tactic_id=attack[0], level=attack[1])
    team.tactics.add(tactic_id=defense[0], level=defense[1])
    team.chosen.attck_id, team.chosen.defense_id = attack[0], defense[0]
    team.power = sum(p.power for p in team.formation.players[:7])
    if power and team.power:
        stat_types = {t for t, _, _ in matches.STATS}
        factor = power / team.power
        for p in team.formation.players:
            for a in p.attr:
                if a.type in stat_types:
                    a.val = int(a.val * factor)
            p.power = matches.card_power(p.attr)
        team.power = sum(p.power for p in team.formation.players[:7])
    return team


# --- challenge list -----------------------------------------------------------------------------
def roll_list(gid: int, own_level: int) -> list[dict]:
    """Up to LIST_SIZE opponents near the own score; real players first, robots fill up."""
    me = ladder(gid)
    snaps = state()["snapshots"]
    others = [int(g) for g in snaps if g != str(gid)]
    others.sort(key=lambda g: abs(ladder(g)["score"] - me["score"]))
    pool = others[:LIST_SIZE * 2]
    picks = random.sample(pool, min(LIST_SIZE, len(pool)))
    out = [{"gid": g} for g in picks]
    row = _robot_row(own_level)
    own_power = state()["snapshots"].get(str(gid), {}).get("power", 0)
    for i in range(LIST_SIZE - len(out)):
        out.append({"gid": -(i + 1), "robot": row.get("ID", 1), "team": random.randrange(4),
                    "score": max(0, me["score"] + random.randint(-60, 60)),
                    "level": row.get("Level", own_level), "name": f"CPU {i + 1}",
                    "power": int(own_power * random.uniform(*ROBOT_POWER))})
    me["list"] = out
    return out


def opponent_team(entry: dict) -> P.PVPTeamInfo | None:
    if entry["gid"] > 0:
        return snapshot_team(entry["gid"])
    return robot_team(entry.get("robot", 1), entry.get("team", 0), entry.get("name", "CPU"), entry["gid"],
                      entry.get("power", 0))


def opponent_score(entry: dict) -> int:
    return ladder(entry["gid"])["score"] if entry["gid"] > 0 else entry.get("score", START_SCORE)


def arena_player(entry: dict, viewer: int = 0) -> P.ArenaPlayer:
    team = opponent_team(entry)
    p = P.ArenaPlayer(gid=entry["gid"], score=opponent_score(entry))
    if entry["gid"] > 0:
        snap = state()["snapshots"].get(str(entry["gid"]), {})
        p.name, p.icon, p.icon_frame = snap.get("name", ""), snap.get("icon", 0), snap.get("frame", 0)
        p.level, p.rank = snap.get("level", 1), rank_of(entry["gid"])
    else:
        p.name, p.level = entry.get("name", "CPU"), entry.get("level", 1)
        p.icon, p.icon_frame = (team.icon, team.icon_frame) if team else (0, 0)
    if team:
        p.power = shown(team.power, entry["gid"], viewer)
        p.team.pvp_formation.CopyFrom(team.formation)
        p.team.power = p.power
    return p


def _stale(gid: int) -> bool:
    """The list still has robots although other players are available that it doesn't show."""
    lst = ladder(gid)["list"]
    shown = {e["gid"] for e in lst}
    free = [g for g in state()["snapshots"] if int(g) != gid and int(g) not in shown]
    return any(e["gid"] < 0 for e in lst) and bool(free)


def arena_info(gid: int, acc) -> bytes:
    me = ladder(gid, acc)
    if not me["list"] or _stale(gid):
        roll_list(gid, acc.level)
    sid, start, end = season()
    info = P.FightArenaInfoS2C()
    info.season.season_id, info.season.start_time, info.season.end_time = sid, start, end
    info.rank_data.rank, info.rank_data.score = rank_of(gid), me["score"]
    c = info.challenge
    c.challenge_times, c.buy_times, c.challenge_up_times = me["times"], me["buys"], me["ups"]
    for entry in me["list"]:
        c.challenge_list.append(arena_player(entry, gid))
    info.gift.hisotry_score = me["history"]
    info.gift.season_gift_got.extend(me["gifts"])
    return info.SerializeToString()


# --- score --------------------------------------------------------------------------------------
def _parm(score: int) -> dict:
    for row in ARENA_PARM:
        lo, hi = row["Score"]
        if lo <= score < hi:
            return row
    return ARENA_PARM[-1] if ARENA_PARM else {"K1": 40, "K4": 4}


def attack_gain(own: int, enemy: int) -> int:
    expected = 1 / (1 + 10 ** ((enemy - own) / 350))
    parm = _parm(own)  # floor K3 as the client's own estimate (ChallengePanelLogic.CalcWinScore)
    return max(math.ceil(parm["K1"] * (1 - expected)), parm.get("K3", 0))


def defense_loss(own: int) -> int:
    return _parm(own)["K4"]


def _record(d: dict, kind: int, enemy: dict, own_score: int, change: int, begin: int):
    d["records"].insert(0, {"type": kind, "enemy": enemy, "own": own_score, "change": change,
                            "begin": begin, "end": int(time.time())})
    del d["records"][MAX_RECORDS:]


def _enemy_card(gid: int, entry: dict | None = None) -> dict:
    if gid > 0:
        snap = state()["snapshots"].get(str(gid), {})
        return {"gid": gid, "name": snap.get("name", ""), "icon": snap.get("icon", 0),
                "frame": snap.get("frame", 0), "power": snap.get("power", 0), "level": snap.get("level", 1),
                "score": ladder(gid)["score"]}
    entry = entry or {}
    team = opponent_team(entry) if entry else None  # robots: the template team's head icon
    return {"gid": gid, "name": entry.get("name", "CPU"), "icon": team.icon if team else 0,
            "frame": team.icon_frame if team else 0, "power": team.power if team else 0,
            "level": entry.get("level", 1), "score": entry.get("score", START_SCORE)}


def settle(gid: int, entry: dict, won: bool, begin: int) -> int:
    """Apply a finished arena fight to both ladders; returns the attacker's score change."""
    me = ladder(gid)
    enemy_gid = entry["gid"]
    enemy_score = opponent_score(entry)
    change = attack_gain(me["score"], enemy_score) if won else 0
    attacker_card = _enemy_card(gid)
    defender_card = _enemy_card(enemy_gid, entry)
    defender_card["power"] = defender_card["power"] or (opponent_team(entry).power if opponent_team(entry) else 0)
    me["score"] += change
    me["history"] = max(me["history"], me["score"])
    _record(me, P.ARENA_CHALLENGE_WIN if won else P.ARENA_CHALLENGE_LOSE, defender_card, me["score"], change, begin)
    if enemy_gid > 0:
        d = ladder(enemy_gid)
        loss = 0 if not won else min(defense_loss(d["score"]), d["score"])
        d["score"] -= loss
        attacker_card["score"] = me["score"]
        _record(d, P.ARENA_DEFENSE_LOSE if won else P.ARENA_DEFENSE_WIN, attacker_card, d["score"], -loss, begin)
    # the beaten list entry is replaced, as in the original (a fresh opponent appears)
    if won:
        me["list"] = []
    return change


def records(gid: int) -> bytes:
    out = P.FightArenaRecordS2C()
    for r in ladder(gid)["records"]:
        e = r["enemy"]
        out.arena_record.add(own_gid=gid, record_type=r["type"], enemy_id=e["gid"], enemy_name=e["name"],
                             enemy_icon=e["icon"], enemy_icon_frame=e["frame"], enemy_power=shown(e["power"], e["gid"], gid),
                             enemy_score=e["score"], own_score=r["own"], own_change_score=r["change"],
                             record_id=-1, begin_time=r["begin"], end_time=r["end"], enemy_level=e["level"])
    return out.SerializeToString()


# --- rooms --------------------------------------------------------------------------------------
class ArenaFight:
    def __init__(self, gid: int, entry: dict):
        self.gid, self.entry, self.begin = gid, entry, int(time.time())


def build_room(args_pb: bytes, acc, gid: int, name: str):
    """(PVPCreateS2C bytes, matches.Match) for an arena challenge, None for other modes."""
    import livematch
    args = P.PVPRoomArgs.FromString(args_pb)
    if args.pvp_type != P.FIGHT_ARENA:
        return None
    room = _room(args, acc, gid, name)
    if room is None:
        return None
    create, entry = room
    info = create.info
    me = ladder(gid, acc)
    me["times"] += 1
    save()
    if livematch.LIBRARY:
        live = livematch.LiveMatch(info, matches.point_chance(info.home.power, info.away.power))
        m = matches.Match(info, False, 0b001, live)
    else:
        won, mask = matches.decide(info.home.power, info.away.power)
        info.preset_match_id = random.choice(matches.WIN_PRESETS) if won else matches.LOSS_PRESET
        m = matches.Match(info, won, mask)
    m.pvp = ArenaFight(gid, entry)
    return create.SerializeToString(), m


def _room(args, acc, gid: int, name: str):
    me = ladder(gid, acc)
    index = args.pvp_args[1] if len(args.pvp_args) > 1 else 0
    if not me["list"]:
        roll_list(gid, acc.level)
    if not 0 <= index < len(me["list"]):
        return None
    entry = me["list"][index]
    away = opponent_team(entry)
    if away is None:
        return None
    now = int(time.time() * 1000)
    create = matches._captured("0x0302", P.PVPCreateS2C)
    info = create.info
    info.room_args.CopyFrom(args)
    home = matches.home_team(info.home, acc, gid, name, P.FIGHT_ARENA)
    prof = profile(acc, gid, name)
    home.icon, home.icon_frame = prof["icon"], prof["frame"]
    info.home.CopyFrom(home)
    info.away.CopyFrom(away)
    while len(info.away.formation.players) < matches.TEAM_SIZE:
        info.away.formation.players.add()
    info.preset_match_id = 0
    info.bo_type, info.bo_score, info.time_end = 1, matches.LIVE_POINTS, 0  # captured arena room: to 5
    info.first_serve_team = False
    info.create_time, info.session, info.seed = now // 1000, now, random.getrandbits(31)
    return create, entry


def finish(match) -> tuple[bytes, list[tuple[int, int]], int]:
    """Arena result: (PVPResultS2C bytes, rewards, score change)."""
    fight = match.pvp
    won = match.won
    old = (ladder(fight.gid)["score"], opponent_score(fight.entry))
    change = settle(fight.gid, fight.entry, won, fight.begin)
    new = (ladder(fight.gid)["score"], opponent_score(fight.entry))
    save()
    rewards = list(WIN_REWARD if won else LOSS_REWARD)
    res = P.PVPResultS2C.FromString(matches.build_result(match.info, won, 0b111 if won else 0, rewards))
    # the arena result screen shows old -> new points of both sides (ArenaSettlementViewLogic)
    for side, o, n in ((res.battle_result.player_grade.home, old[0], new[0]),
                       (res.battle_result.player_grade.away, old[1], new[1])):
        side.arena_score.old_score, side.arena_score.new_score = o, n
    return res.SerializeToString(), rewards, change


# --- message handlers ---------------------------------------------------------------------------
# set by server.py: grant_rewards(client, rewards, popup), player_name(client), generic(client, pb, id),
# mission_event(client, code, amount, params)
HOOKS = {}


def _name(client) -> str:
    return HOOKS["player_name"](client)


def on_login(client) -> list[tuple[int, bytes]]:
    """Snapshot the player's team and replace the captured arena info with this server's."""
    acc = client.account
    snapshot(acc, client.gid, _name(client))
    ladder(client.gid, acc)
    save()
    return [(MSG_ARENA_INFO, arena_info(client.gid, acc))]


async def handle_info(client, pb: bytes):
    acc = client.account
    snapshot(acc, client.gid, _name(client))
    out = arena_info(client.gid, acc)
    save()
    await client.send_packet(MSG_ARENA_INFO, out)


async def handle_refresh(client, pb: bytes):
    me = ladder(client.gid, client.account)
    err = 0
    if me["ups"] >= REFRESH_MAX:
        err = P.FIGHT_ARENA_CHALLENGE_TIME_OUT if hasattr(P, "FIGHT_ARENA_CHALLENGE_TIME_OUT") else 1
    else:
        me["ups"] += 1
        roll_list(client.gid, client.account.level)
    out = arena_info(client.gid, client.account)
    save()
    await client.send_packet(MSG_ARENA_UP, P.FightArenaChallengeUpS2C(err=err).SerializeToString())
    await client.send_packet(MSG_ARENA_INFO, out)


async def handle_buy(client, pb: bytes):
    num = max(1, P.FightArenaChallengeBuyC2S.FromString(pb).num)
    me = ladder(client.gid, client.account)
    acc = client.account
    num = min(num, BUY_MAX - me["buys"])
    cost = sum(BUY_PRICES[min(me["buys"] + i, len(BUY_PRICES) - 1)] for i in range(num)) if num > 0 else 0
    if num <= 0 or acc.get_currency(DIAMONDS) < cost:
        await client.send_packet(MSG_ARENA_BUY, P.FightArenaChallengeBuyS2C(err=1, buy_times=me["buys"])
                                 .SerializeToString())
        return
    entry = acc.set_currency(DIAMONDS, acc.get_currency(DIAMONDS) - cost)
    acc.save()
    me["buys"] += num
    save()
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, entry)]))
    await client.send_packet(MSG_ARENA_BUY, P.FightArenaChallengeBuyS2C(buy_times=me["buys"]).SerializeToString())
    await client.send_packet(MSG_ARENA_INFO, arena_info(client.gid, acc))


async def handle_record(client, pb: bytes):
    await client.send_packet(MSG_ARENA_RECORD, records(client.gid))


async def handle_lineup(client, pb: bytes):
    see = P.FightArenaPlayerLineupC2S.FromString(pb).see_gid
    me = ladder(client.gid, client.account)
    entry = next((e for e in me["list"] if e["gid"] == see), {"gid": see})
    team = opponent_team(entry) if see else None
    out = P.FightArenaPlayerLineupS2C()
    if team:
        out.team.pvp_formation.CopyFrom(team.formation)
        out.team.power = shown(team.power, see, client.gid)
    else:
        out.err = 1
    await client.send_packet(MSG_ARENA_LINEUP, out.SerializeToString())


async def handle_gift(client, pb: bytes):
    """Season chest: gift_index = arenaladder ID reached by the highest score this season."""
    idx = P.FightArenaGiftC2S.FromString(pb).gift_index
    me = ladder(client.gid, client.account)
    row = next((r for r in ARENA_LADDER if r["ID"] == idx), None)
    if not row or idx in me["gifts"] or me["history"] < row["Score"][0]:
        await client.send_packet(MSG_ARENA_GIFT, P.FightArenaGiftS2C(err=1).SerializeToString())
        return
    me["gifts"].append(idx)
    save()
    rewards = [tuple(int(x) for x in r.split(":")) for r in row.get("Reward") or []]
    await client.send_packet(MSG_ARENA_GIFT, P.FightArenaGiftS2C().SerializeToString())
    await HOOKS["grant_rewards"](client, rewards, True)
    await client.send_packet(MSG_ARENA_INFO, arena_info(client.gid, client.account))


def _rank_elem(gid: int, place: int) -> P.RankListElem:
    snap = state()["snapshots"].get(str(gid), {})
    e = P.RankListElem(gid=gid, name=snap.get("name", ""), icon=snap.get("icon", 0),
                       icon_frame=snap.get("frame", 0), level=snap.get("level", 1), rank=place,
                       score=ladder(gid)["score"], power=shown(snap.get("power", 0), gid))
    team = snapshot_team(gid)
    if team:
        e.team.pvp_formation.CopyFrom(team.formation)
        e.team.power = shown(team.power, gid)
    return e


def rank_list(gid: int) -> P.RankListData:
    order = sorted(state()["arena"].items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    data = P.RankListData()
    for i, (g, _) in enumerate(order[:RANK_PAGE]):
        data.rank.append(_rank_elem(int(g), i + 1))
    data.own.CopyFrom(_rank_elem(gid, rank_of(gid)))
    return data


async def handle_rank_list(client, pb: bytes):
    req = P.RankListC2S.FromString(pb)
    if req.rank_type != P.RANK_ARENA:
        await HOOKS["generic"](client, pb, MSG_RANK_LIST)
        return
    ladder(client.gid, client.account)
    out = P.RankListS2C(rank_type=P.RANK_ARENA)
    out.rank_data.CopyFrom(rank_list(client.gid))
    await client.send_packet(MSG_RANK_LIST, out.SerializeToString())


async def handle_rank_self(client, pb: bytes):
    req = P.RankSelfC2S.FromString(pb)
    if req.rank_type != P.RANK_ARENA:
        await HOOKS["generic"](client, pb, MSG_RANK_SELF)
        return
    ladder(client.gid, client.account)
    out = P.RankSelfS2C(rank_type=P.RANK_ARENA, params=req.params)
    out.own.CopyFrom(_rank_elem(client.gid, rank_of(client.gid)))
    await client.send_packet(MSG_RANK_SELF, out.SerializeToString())


async def handle_quick(client, pb: bytes):
    """0x0306 PVPQuickBattleC2S: decided at once (own team clearly stronger)."""
    req = P.PVPQuickBattleC2S.FromString(pb)
    if req.args.pvp_type == P.FIGHT_PEAK:  # Triple Clash: one game of a best-of-three series
        import peak
        await peak.handle_quick(client, pb)
        return
    if req.args.pvp_type != P.FIGHT_ARENA:
        await HOOKS["generic"](client, pb, MSG_QUICK)
        return
    acc = client.account
    room = _room(req.args, acc, client.gid, _name(client))
    if room is None:
        await client.send_packet(MSG_QUICK, P.PVPQuickBattleS2C(error=1).SerializeToString())
        return
    create, entry = room
    calibrate(client.gid, req.home_power, create.info.home.power)
    ladder(client.gid, acc)["times"] += 1
    won = random.random() < matches.win_chance(create.info.home.power, create.info.away.power)
    m = matches.Match(create.info, won, 0b111 if won else 0)
    m.pvp = ArenaFight(client.gid, entry)
    result, rewards, _ = finish(m)
    out = P.PVPQuickBattleS2C()
    out.battle_result.CopyFrom(P.PVPResultS2C.FromString(result).battle_result)
    await client.send_packet(MSG_QUICK, out.SerializeToString())
    await HOOKS["grant_rewards"](client, rewards, False)
    await client.send_packet(MSG_ARENA_INFO, arena_info(client.gid, acc))


async def handle_result(client, match):
    """0x0304 for an arena room (called by server.handle_pvp_result)."""
    result, rewards, change = finish(match)
    await client.send_packet(0x0304, result)
    await HOOKS["grant_rewards"](client, rewards, False)
    await client.send_packet(MSG_ARENA_INFO, arena_info(client.gid, client.account))
    return change


HANDLERS = {
    MSG_ARENA_INFO: handle_info,
    MSG_ARENA_UP: handle_refresh,
    MSG_ARENA_GIFT: handle_gift,
    MSG_ARENA_RECORD: handle_record,
    MSG_ARENA_BUY: handle_buy,
    MSG_ARENA_LINEUP: handle_lineup,
    MSG_RANK_LIST: handle_rank_list,
    MSG_RANK_SELF: handle_rank_self,
    MSG_QUICK: handle_quick,
}
