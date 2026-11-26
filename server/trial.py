"""
Specialty Trial (client: AttributeTrial, German "Spezialitätsprüfung", FIGHT_SPECIFIC_TRIAL = 7), per player.

Before, every account got the owner's captured state (0x2901: degree 4, score 144 094 556) and nothing
changed when stages were played. Rules as the client reads them (spec: decoded IL, two captures):

  0x2901 SpecificTrialInfoS2C (server -> client only, at login and after every change)
    info {degree: tier 1-3, 4 = Hell; group_id: specifictrialconf / specificrewardconf GroupId;
          formidable: Hell score; reward_received: bit (specificrewardconf ID & 63) per claimed reward;
          re_time: last weekly reset}
    stages [{stage_type, stage_group_id = specifictrialconf ID, progress = stages cleared this week,
             history_progress = best progress of earlier weeks (quick win)}]
    open_types: attribute types open today (specifictrialconf OpenWeekDay, day starts 06:00); the client
             needs at least one, and in Hell the one unit of this week's Hell type (else it crashes)
  0x2902 SpecificTrialUpdateS2C{formidable}   0x2903 rewards {id 0 = all reached} -> {err, reward_received}

Normal tiers: a win raises the row's progress by one (the current stage is Stage[progress]). Hell: a score
match (own team's summed attack + defence power, like the club boss); formidable = the week's best
score; rewards are the group's specificrewardconf rows with Condition <= formidable. Quick win (0x0306
type 7) when progress < history_progress: the stage counts as won without playing it.
Week (Monday 06:00): progress -> 0 (history kept), degree +1 if last week's tier was fully cleared
(max 4), formidable and rewards -> 0, Hell type = ISO week % 4 + 1 (fits both captures).
Ranking: RankList type 18 (RANK_FIGHT_SPECIFIC_TRIAL), score = formidable (pvp.PLAYER_RANKS).
State: pvp.json "trial".
"""
import time

import matches
import pvp

P = matches.P

MSG_INFO, MSG_UPDATE, MSG_REWARD = 0x2901, 0x2902, 0x2903
HELL = 4
DAY_START = 6 * 3600  # the game's day and week begin at 06:00
ROWS = {r["ID"]: r for r in pvp._cfg("specifictrialconf")}
GROUPS = pvp._cfg("specifictrialgroupconf")
REWARDS = pvp._cfg("specificrewardconf")


def _stages(row: dict) -> list[int]:
    s = row.get("Stage") or []
    return s if isinstance(s, list) else [s]


def _days(row: dict) -> list[int]:
    d = row.get("OpenWeekDay") or []
    return d if isinstance(d, list) else [d]


STAGE_ROW = {s: r["ID"] for r in ROWS.values() for s in _stages(r)}


def _game_time(now: float | None = None) -> time.struct_time:
    return time.localtime((time.time() if now is None else now) - DAY_START)


def _week(now: float | None = None) -> str:
    return time.strftime("%G-%V", _game_time(now))  # ISO week of the game day


def _weekday(now: float | None = None) -> int:
    return _game_time(now).tm_wday + 1  # Monday = 1


def current_group(now: float | None = None) -> int:
    now = time.time() if now is None else now
    best = 1
    for g in GROUPS:
        opens = g.get("OpenTime") or ""
        t = time.mktime(time.strptime(opens, "%Y-%m-%d_%H:%M:%S")) if opens else 0
        if t <= now:
            best = max(best, g["ID"])
    return best


def row_of(group: int, kind: int, degree: int) -> dict | None:
    return next((r for r in ROWS.values() if r.get("GroupId") == group and r.get("Type") == kind
                 and r.get("SpecificTrialDifficulty") == degree), None)


def hell_type(now: float | None = None) -> int:
    return int(time.strftime("%V", _game_time(now))) % 4 + 1


def _store() -> dict:
    return pvp.state().setdefault("trial", {})


def _initial(acc) -> dict:
    """A new player starts at tier 1; the owner keeps the captured tier and score."""
    d = {"week": "", "degree": 1, "formidable": 0, "rewards": 0, "units": {}, "re_time": int(time.time()),
         "group": current_group()}
    import accounts
    try:
        if acc is not None and acc.b is accounts.CAPTURED:
            cap = P.SpecificTrialInfoS2C.FromString(acc.b.pb("0x2901"))
            d.update(degree=cap.info.degree or 1, formidable=cap.info.formidable, rewards=cap.info.reward_received,
                     week=_week(), units={str(u.stage_group_id): {"progress": u.progress, "history": u.history_progress}
                                          for u in cap.stages})
    except Exception:
        pass
    return d


def state(gid: int, acc=None, now: float | None = None) -> dict:
    """The player's trial state, weekly reset applied."""
    st = _store()
    d = st.get(str(gid))
    if d is None:
        d = st[str(gid)] = _initial(acc)
    week = _week(now)
    if d["week"] != week:
        if d["week"]:  # a new week: tier up if last week's tier was fully cleared
            done = [row_of(d["group"], k, d["degree"]) for k in range(1, 5)]
            if d["degree"] < HELL and all(r and d["units"].get(str(r["ID"]), {}).get("progress", 0) >= len(_stages(r))
                                          for r in done):
                d["degree"] += 1
        for u in d["units"].values():
            u["history"] = max(u.get("history", 0), u.get("progress", 0))
            u["progress"] = 0
        d.update(week=week, formidable=0, rewards=0, re_time=int(time.time() if now is None else now),
                 group=current_group(now))
    return d


def open_types(d: dict, now: float | None = None) -> list[int]:
    if d["degree"] >= HELL:
        return [hell_type(now)]
    day = _weekday(now)
    out = [k for k in range(1, 5) if day in _days(row_of(d["group"], k, d["degree"]) or {})]
    return out or [1]


def info(gid: int, acc=None, now: float | None = None) -> bytes:
    d = state(gid, acc, now)
    out = P.SpecificTrialInfoS2C()
    out.info.CopyFrom(P.SpecificTrialData(degree=d["degree"], re_time=d["re_time"], formidable=d["formidable"],
                                          reward_received=d["rewards"], group_id=d["group"]))
    kinds = open_types(d, now) if d["degree"] >= HELL else range(1, 5)
    for k in kinds:
        row = row_of(d["group"], k, d["degree"])
        if row:
            u = d["units"].get(str(row["ID"]), {})
            out.stages.add(stage_type=k, stage_group_id=row["ID"], progress=u.get("progress", 0),
                           history_progress=u.get("history", 0))
    out.open_types.extend(open_types(d, now))
    return out.SerializeToString()


def _push(gid: int, msg_id: int, pb: bytes):
    import mail
    import asyncio
    client = mail.ONLINE.get(int(gid))
    if client is None:
        return
    try:
        asyncio.get_running_loop().create_task(client.send_packet(msg_id, pb))
    except RuntimeError:
        pass


def _unit(d: dict, stage: int) -> tuple[dict | None, dict | None]:
    row = ROWS.get(STAGE_ROW.get(stage, 0))
    if not row:
        return None, None
    return row, d["units"].setdefault(str(row["ID"]), {"progress": 0, "history": 0})


def on_result(gid: int, acc, stage: int, won: bool, match) -> list[tuple[int, bytes]]:
    """After a FIGHT_SPECIFIC_TRIAL match: progress (normal tiers) or score (Hell); update messages."""
    d = state(gid, acc)
    row, u = _unit(d, stage)
    if row is None:
        return []
    if row.get("SpecificTrialDifficulty") == HELL:
        import clubs
        score = clubs.boss_score(match)
        d["formidable"] = max(d["formidable"], score)
        u["progress"] = 1
        pvp.save()
        return [(MSG_UPDATE, P.SpecificTrialUpdateS2C(formidable=d["formidable"]).SerializeToString()),
                (MSG_INFO, info(gid, acc))]
    stages = _stages(row)
    if won and u["progress"] < len(stages) and stages[u["progress"]] == stage:
        u["progress"] += 1
        u["history"] = max(u["history"], u["progress"])
    pvp.save()
    return [(MSG_INFO, info(gid, acc))]


async def handle_quick(client, req) -> None:
    """0x0306 PVPQuickBattleC2S type 7: the current stage counts as won (offered while the row's
    progress is below the best of earlier weeks); stage rewards; reply with pvp_type 7."""
    gid, acc = int(client.gid), client.account
    d = state(gid, acc)
    stage = req.args.pvp_args[1] if len(req.args.pvp_args) > 1 else 0
    row, u = _unit(d, stage)
    out = P.PVPQuickBattleS2C()
    out.battle_result.info.room_args.CopyFrom(req.args)
    stages = _stages(row) if row else []
    if (row is None or row.get("SpecificTrialDifficulty") == HELL or u["progress"] >= u["history"]
            or u["progress"] >= len(stages) or stages[u["progress"]] != stage):
        out.error = 1
        await client.send_packet(0x0306, out.SerializeToString())
        return
    rewards = matches.roll_stage_rewards(stage, False)
    for conf, n in rewards:
        out.battle_result.rewards.add(conf_id=conf, number=n)
    u["progress"] += 1
    pvp.save()
    await client.send_packet(0x0306, out.SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, rewards, True)
    await client.send_packet(MSG_INFO, info(gid, acc))


async def handle_reward(client, pb: bytes):
    """0x2903 SpecificTrialRewardC2S{id}: claim every reached Hell reward (id 0 = all)."""
    gid, acc = int(client.gid), client.account
    want = P.SpecificTrialRewardC2S.FromString(pb).id
    d = state(gid, acc)
    rows = [r for r in REWARDS if r.get("GroupId") == d["group"] and r.get("Condition", 0) <= d["formidable"]
            and not d["rewards"] >> (r["ID"] & 63) & 1 and (want == 0 or r["ID"] == want)]
    if not rows:
        await client.send_packet(MSG_REWARD, P.SpecificTrialRewardS2C(err=1, reward_received=d["rewards"])
                                 .SerializeToString())
        return
    import mail
    items: dict[int, int] = {}
    for r in rows:
        d["rewards"] |= 1 << (r["ID"] & 63)
        for conf, n in mail.items_of(r.get("Reward")):
            items[conf] = items.get(conf, 0) + n
    pvp.save()
    await client.send_packet(MSG_REWARD, P.SpecificTrialRewardS2C(reward_received=d["rewards"]).SerializeToString())
    await pvp.HOOKS["grant_rewards"](client, list(items.items()), True)


def score(gid: int) -> int:
    """Ranking value (RANK_FIGHT_SPECIFIC_TRIAL): this week's Hell score."""
    d = _store().get(str(gid))
    return state(gid)["formidable"] if d else 0


HANDLERS = {MSG_REWARD: handle_reward}
