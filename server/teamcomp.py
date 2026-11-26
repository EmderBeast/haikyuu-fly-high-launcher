"""
Match Streak (TeamCompetition, FIGHT_TEAMCOMPETITION = 12): PvE competitions of 2-3 stages in a
row against stage monster teams, each stage with its own line-up; stars for the total lead.

Client flow (hotfix TeamCompetition.*, MatchFeature.TeamCompetitionMatch*):
  0x4001 TeamCompetitionInfo   groups (teamcompetitiongroupconf) with their competitions
                               (teamcompetitionconf: star / first_reward / pass), group types,
                               the Match Streak line-ups (TeamFormation, one per team index)
  0x4002 Update                changed groups (competitions, claimed star chests)
  0x4003 TakeReward            star chest of a group (teamcompetitionrewardconf: StarCondition)
  0x4004 FormationSave         line-ups
  Challenge: 0x0302 with pvp_args [team index, stage id] for the competition's first stage; after
  each stage the client shows a half-time screen and restarts with the next stage
  (TeamCompetitionUtils.StartNextStage); after the last one it opens the result screen, which reads
  the competition's stars from the group data. So the update (0x4002) goes out before the last
  0x0304.
Stars: StarTask lists "lead the opponent by N points" for stars 1-3 (custom language 1050); the
lead is the total over the competition's stages. Passing = total lead > 0. The competition's
Reward is paid on the first pass.
"""
import random
import time

import matches
import pvp
import wire
from accounts import CAPTURED

P = matches.P

MSG_INFO, MSG_UPDATE, MSG_TAKE_REWARD, MSG_FORMATION_SAVE = 0x4001, 0x4002, 0x4003, 0x4004
SLOTS = 13
COURT = 7
TEAMS = 3

COMPS = {r["ID"]: r for r in pvp._cfg("teamcompetitionconf")}
GROUPS = {r["ID"]: r for r in pvp._cfg("teamcompetitiongroupconf")}
STAR_REWARDS = {r["ID"]: r for r in pvp._cfg("teamcompetitionrewardconf")}


def _flat(v) -> list:
    out = []
    for x in v if isinstance(v, list) else []:
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def kv(v) -> list[tuple[int, int]]:
    """Config map lists [[8, k, 16, v], 8, k, 16, v, ...] (tag 8 = key, 16 = value, 24 = extra)."""
    toks, out, key = _flat(v), [], None
    for i in range(0, len(toks) - 1, 2):
        tag, val = toks[i], toks[i + 1]
        if tag == 8:
            key = val
        elif tag == 16 and key is not None:
            out.append((key, val))
            key = None
    return out


def comp_stages(cid: int) -> list[int]:
    return [k for k, _ in kv(COMPS.get(cid, {}).get("StageId"))]


def star_leads(cid: int) -> list[int]:
    return [v for _, v in sorted(kv(COMPS.get(cid, {}).get("StarTask")))]


STAGE_TO_COMP = {s: cid for cid in COMPS for s in comp_stages(cid)}


def _time(text: str) -> float | None:
    if not text:
        return None
    return time.mktime(time.strptime(text, "%Y-%m-%d_%H:%M:%S"))


def visible_groups(now=None) -> list[int]:
    """Groups that are permanent or not over yet (the client shows them by their open time)."""
    now = time.time() if now is None else now
    out = []
    for gid_, g in sorted(GROUPS.items()):
        close = _time(g.get("CloseTime", ""))
        if close is None or close > now:
            out.append(gid_)
    return out


# --- state --------------------------------------------------------------------------------------
def _captured_info() -> P.TeamCompetitionInfoS2C:
    return P.TeamCompetitionInfoS2C.FromString(CAPTURED.pb("0x4001"))


def entry(gid: int, acc=None) -> dict:
    tc = pvp.state().setdefault("teamcomp", {})
    d = tc.get(str(gid))
    if d is None:
        d = {"comps": {}, "chests": {}, "formations": None, "series": {}}
        if acc is not None and acc.b is CAPTURED:  # the owner keeps the captured progress
            info = _captured_info()
            for g in info.competition_groups:
                for c in g.competitions:
                    passed = bool(getattr(c, "pass"))  # "pass" is a Python keyword
                    if c.star or passed or c.first_reward:
                        d["comps"][str(c.competition_id)] = {"star": c.star, "first": c.first_reward,
                                                             "pass": passed}
            try:
                upd = matches._captured("0x4002", P.TeamCompetitionUpdateS2C)
                for g in upd.competition_groups:
                    d["chests"][str(g.competition_group_id)] = sorted(set(g.star_reward_ids))
            except Exception:
                pass
        tc[str(gid)] = d
    return d


def _card_power(acc, inst: int) -> int:
    raw = acc.get_card(inst)
    return matches.card_entry(P.PlayerCard.FromString(raw)).power if raw else 0


def default_formations(acc) -> list[dict]:
    if acc.b is CAPTURED:
        forms = list(_captured_info().formations)
        if forms:
            return [{"lineup": f.lineup, "insts": list(f.inst_ids)} for f in forms]
    import peak
    insts = [i for i in peak._all_cards(acc) if acc.get_card(i)]
    insts.sort(key=lambda i: -_card_power(acc, i))
    out = []
    for t in range(TEAMS):
        pick = [insts[(t * COURT + k) % len(insts)] for k in range(COURT)] if insts else []
        out.append({"lineup": t, "insts": pick + [0] * (SLOTS - len(pick))})
    return out


def formations(gid: int, acc) -> list[dict]:
    d = entry(gid, acc)
    if not d.get("formations"):
        d["formations"] = default_formations(acc)
    return d["formations"]


def team_formation(f: dict) -> P.TeamFormation:
    return P.TeamFormation(lineup=f["lineup"], inst_ids=f["insts"])


# --- messages -----------------------------------------------------------------------------------
def group_msg(gid: int, group_id: int) -> P.TeamCompetitionGroup:
    d = entry(gid)
    g = P.TeamCompetitionGroup(competition_group_id=group_id)
    for cid in GROUPS.get(group_id, {}).get("StageGroupId") or []:
        st = d["comps"].get(str(cid), {})
        c = g.competitions.add(competition_id=cid, star=st.get("star", 0), first_reward=st.get("first", False))
        setattr(c, "pass", st.get("pass", False))
    g.star_reward_ids.extend(d["chests"].get(str(group_id), []))
    return g


def _group_types() -> list:
    try:
        return list(_captured_info().competition_group_types)
    except Exception:
        return []


def info(gid: int, acc) -> bytes:
    entry(gid, acc)
    out = P.TeamCompetitionInfoS2C()
    for group_id in visible_groups():
        out.competition_groups.append(group_msg(gid, group_id))
    out.competition_group_types.extend(_group_types())
    for f in formations(gid, acc):
        out.formations.append(team_formation(f))
    return out.SerializeToString()


def update(gid: int, group_ids) -> bytes:
    out = P.TeamCompetitionUpdateS2C()
    for group_id in group_ids:
        out.competition_groups.append(group_msg(gid, group_id))
    return out.SerializeToString()


def group_of(cid: int) -> int:
    return next((g for g, row in GROUPS.items() if cid in (row.get("StageGroupId") or [])), 0)


# --- matches ------------------------------------------------------------------------------------
class StreakGame:
    def __init__(self, gid: int, comp: int, index: int, team: int, stage: int):
        self.gid, self.comp, self.index, self.team, self.stage = gid, comp, index, team, stage


def build_room(args_pb: bytes, acc, gid: int, name: str):
    """Live stage of a competition (0x0302 with FIGHT_TEAMCOMPETITION), None for other modes."""
    import livematch
    args = P.PVPRoomArgs.FromString(args_pb)
    if args.pvp_type != P.FIGHT_TEAMCOMPETITION or len(args.pvp_args) < 2:
        return None
    team, stage = args.pvp_args[0], args.pvp_args[1]
    comp = STAGE_TO_COMP.get(stage)
    row = matches.stage_row(stage)
    if not comp or not row or not matches._num(row.get("MonsterTeam")):
        return None
    index = comp_stages(comp).index(stage)
    d = entry(gid, acc)
    if index == 0 or d.get("series", {}).get("comp") != comp:
        d["series"] = {"comp": comp, "scores": [], "begin": int(time.time())}
    forms = formations(gid, acc)
    form = next((f for f in forms if f["lineup"] == team), forms[min(index, len(forms) - 1)] if forms else None)
    now = int(time.time() * 1000)
    create = matches._captured("0x0302", P.PVPCreateS2C)
    info_ = create.info
    info_.room_args.CopyFrom(args)
    import peak
    prof = pvp.profile(acc, gid, name)
    info_.home.CopyFrom(peak.build_team(acc, form["insts"] if form else [], gid, name, (prof["icon"], prof["frame"])))
    info_.away.CopyFrom(matches.away_team(info_.away, stage, now))
    info_.preset_match_id = 0
    info_.bo_type = 1
    info_.bo_score = matches._num(row.get("BoScore"), matches.LIVE_POINTS) or matches.LIVE_POINTS
    info_.time_end = 0
    info_.first_serve_team = False
    info_.create_time, info_.session, info_.seed = now // 1000, now, random.getrandbits(31)
    pvp.save()
    if livematch.LIBRARY:
        live = livematch.LiveMatch(info_, matches.point_chance(info_.home.power, info_.away.power))
        m = matches.Match(info_, False, 0b001, live)
    else:
        won = random.random() < matches.win_chance(info_.home.power, info_.away.power)
        m = matches.Match(info_, won, 0b001)
    m.streak = StreakGame(gid, comp, index, team, stage)
    return create.SerializeToString(), m


def settle(gid: int, comp: int, scores: list) -> dict:
    """Competition finished: stars from the total lead, pass, first-pass reward."""
    d = entry(gid)
    lead = sum(h for h, _ in scores) - sum(a for _, a in scores)
    passed = lead > 0
    stars = sum(1 for need in star_leads(comp) if passed and lead >= need)
    rec = d["comps"].setdefault(str(comp), {"star": 0, "first": False, "pass": False})
    rewards = []
    if passed and not rec["first"]:
        rec["first"] = True
        rewards = kv(COMPS.get(comp, {}).get("Reward"))
    rec["pass"] = rec["pass"] or passed
    rec["star"] = max(rec["star"], stars)
    d["series"] = {}
    return {"lead": lead, "pass": passed, "stars": stars, "rewards": rewards}


async def handle_result(client, match):
    """0x0304 for a stage (called by server.handle_pvp_result); returns (h, a, settled or None)."""
    game = match.streak
    d = entry(game.gid)
    live = match.live
    bo = match.info.bo_score or matches.LIVE_POINTS
    h, a = (live.score[0], live.score[1]) if live else ((bo, 0) if match.won else (0, bo))
    series = d.get("series") or {"comp": game.comp, "scores": [], "begin": int(time.time())}
    series["scores"].append([h, a])
    d["series"] = series
    if live and not live.over:  # skipped: the client ends the whole competition as a defeat
        if a <= h:  # shown as a lost stage
            a = max(bo, h + 1)
        d["series"] = {}
        settled = {"lead": 0, "pass": False, "stars": 0, "rewards": []}
    else:
        last = game.index >= len(comp_stages(game.comp)) - 1
        settled = settle(game.gid, game.comp, series["scores"]) if last else None
    won = settled["pass"] if settled else h > a
    stars = settled["stars"] if settled else 0
    res = P.PVPResultS2C.FromString(matches.build_result(match.info, won, (1 << stars) - 1 if stars else 0, [],
                                                                matches.played_of(match)))
    br = res.battle_result
    for side, score in ((br.result.home, h), (br.result.away, a)):
        if not side.bo_statistics:
            side.bo_statistics.add()
        side.bo_statistics[0].score = score
    br.result.winner = 1 if h > a else 2
    pvp.save()
    if settled:  # the result screen reads the stars from the group data
        await client.send_packet(MSG_UPDATE, update(game.gid, [group_of(game.comp)]))
    await pvp.HOOKS["before_result"](client, res.SerializeToString(), match.info)
    await client.send_packet(0x0304, res.SerializeToString())
    if settled and settled["rewards"]:
        await pvp.HOOKS["grant_rewards"](client, settled["rewards"], True)
    return h, a, settled


async def handle_info(client, pb: bytes):
    out = info(client.gid, client.account)
    pvp.save()
    await client.send_packet(MSG_INFO, out)


async def handle_formation_save(client, pb: bytes):
    acc = client.account
    req = P.TeamCompetitionFormationSaveC2S.FromString(pb)
    forms = formations(client.gid, acc)
    by_lineup = {f["lineup"]: f for f in forms}
    for f in req.formation:
        insts = [i if acc.get_card(i) else 0 for i in f.inst_ids][:SLOTS]
        by_lineup[f.lineup] = {"lineup": f.lineup, "insts": insts + [0] * (SLOTS - len(insts))}
    entry(client.gid, acc)["formations"] = [by_lineup[k] for k in sorted(by_lineup)]
    pvp.save()
    out = P.TeamCompetitionFormationSaveS2C()
    out.formation.extend(req.formation)
    out.module.extend(req.module)
    await client.send_packet(MSG_FORMATION_SAVE, out.SerializeToString())


def group_stars(gid: int, group_id: int) -> int:
    d = entry(gid)
    return sum(d["comps"].get(str(c), {}).get("star", 0) for c in GROUPS.get(group_id, {}).get("StageGroupId") or [])


async def handle_take_reward(client, pb: bytes):
    req = P.TeamCompetitionTakeRewardC2S.FromString(pb)
    d = entry(client.gid, client.account)
    group_id, rid = req.competition_group_id, req.star_reward_id
    row = STAR_REWARDS.get(rid)
    got = d["chests"].setdefault(str(group_id), [])
    if not row or row.get("TeamCompetitionGroupId") != group_id or rid in got \
            or group_stars(client.gid, group_id) < row.get("StarCondition", 0):
        await client.send_packet(MSG_TAKE_REWARD, P.TeamCompetitionTakeRewardS2C(err=1).SerializeToString())
        return
    got.append(rid)
    pvp.save()
    await client.send_packet(MSG_TAKE_REWARD, P.TeamCompetitionTakeRewardS2C().SerializeToString())
    await client.send_packet(MSG_UPDATE, update(client.gid, [group_id]))
    await pvp.HOOKS["grant_rewards"](client, kv(row.get("Reward")), True)


def on_login(client) -> list[tuple[int, bytes]]:
    out = info(client.gid, client.account)
    pvp.save()
    return [(MSG_INFO, out)]


HANDLERS = {
    MSG_TAKE_REWARD: handle_take_reward,
    MSG_FORMATION_SAVE: handle_formation_save,
}
