"""
Mini games ("Casual Games", client Arder: 0x2B01 ArderInfo, 0x2B03 ArderResult), per player.

Before, the login replayed the owner's captured 0x2B01 and 0x2B03 got no own answer.

Evidenced (capture captures/garena4, 26.11.2026, one game of mini game 1 with 50 points, n 51 / 177-179):
  - request 0x2B03 ArderResultC2S {game_id 1, score 50, ball_not_fall_id 1, ball_not_fall_catch 1};
  - answers in this order: 0x2B01 ArderInfoS2C without `all`, the whole week data and every game, with
    week score 17 600 -> 17 650 (the week score is the sum of the points of every game played) and the
    game's max_score kept (4480 > 50); 0x0E02 TaskChange {6000006 progress 50} (it stood at 0 in that
    login's 0x0E01); then 0x2B03 ArderResultS2C {game_id, score, max_score};
  - mission 6000006 (taskdepotconfig, TaskSubTargetType 80008, Value 800, weekly) reads "Reach 800 Points in
    a Single Set of Casual Games": its progress is the best single game, so max(progress, score).
Assumed (not in a capture): max_score rises to a better score; a game id that is not in minigame.json is
refused with err FAILED and changes nothing; the 80008 mission counts every mini game (its condition 1 is
not a game id we could check, only game 1 was played); the ball_not_fall_* fields change nothing.
Open: when the week score and `unix` reset (the captured unix 1795459571 = Mon 23.11.2026 18:46 UTC is not
a fixed hour), claiming the week rewards (0x2B02, still replayed), a game played for the first time.
State: pvp.json "minigame" {gid: {"week": {score, unix, received, level}, "games": {id: max_score}}}.
"""
import gameconfig
import pvp
import wire

MSG_INFO, MSG_REWARD, MSG_RESULT = 0x2B01, 0x2B02, 0x2B03
SINGLE_GAME_SCORE = 80008  # mission condition "Reach N Points in a Single Set of Casual Games"
GAMES = {int(r["ID"]) for r in pvp._cfg("minigame") if r.get("ID") is not None}


def _P():
    import matches
    return matches.P


def state(gid: int, acc=None) -> dict:
    """This player's mini game state; the first time from the account's own login data (baseline)."""
    st = pvp.state().setdefault("minigame", {})
    d = st.get(str(gid))
    if d is None:
        d = st[str(gid)] = {"week": {"score": 0, "unix": 0, "received": 0, "level": 0}, "games": {}}
        if acc is not None:
            try:
                cap = _P().ArderInfoS2C.FromString(acc.b.pb("0x2B01"))
                w = cap.data
                d["week"] = {"score": w.score, "unix": w.unix, "received": w.received, "level": w.level}
                d["games"] = {str(g.id): g.max_score for g in cap.game_data}
            except Exception:
                pass
    return d


def info(gid: int, acc=None, all_: bool = True) -> bytes:
    d = state(gid, acc)
    w = d["week"]
    out = _P().ArderInfoS2C(all=all_)
    out.data.score, out.data.unix, out.data.received, out.data.level = w["score"], w["unix"], w["received"], w["level"]
    for gid_, best in sorted(d["games"].items(), key=lambda kv: int(kv[0])):
        out.game_data.add(id=int(gid_), max_score=best)
    return out.SerializeToString()


def play(d: dict, game: int, score: int) -> int:
    """Books one game: week score + score, best score of the game. Returns the game's max_score."""
    d["week"]["score"] += score
    best = max(d["games"].get(str(game), 0), score)
    d["games"][str(game)] = best
    return best


def mission_progress(missions, score: int):
    """Open 80008 missions: progress = best single game this period (capped at the target)."""
    for tid in missions.acc.all_task_ids():
        cfg = gameconfig.TASKS.get(tid, {})
        if cfg.get("TaskSubTargetType") != SINGLE_GAME_SCORE or missions.acc.task_state(tid) != 0:
            continue
        old = wire.get(wire.parse(missions.acc.task(tid)), 3, 0)
        target = cfg.get("Value") or 1
        new = min(max(old, score), target)
        if new != old:
            from accounts import TASK_FINISH
            missions._set(tid, TASK_FINISH if new >= target else 0, new)


async def handle_result(client, pb: bytes):
    P = _P()
    gid, acc = int(client.gid), client.account
    req = P.ArderResultC2S.FromString(pb)
    d = state(gid, acc)
    if req.game_id not in GAMES or req.score < 0:
        await client.send_packet(MSG_RESULT, P.ArderResultS2C(err=P.FAILED, game_id=req.game_id).SerializeToString())
        return
    best = play(d, req.game_id, req.score)
    pvp.save()
    await client.send_packet(MSG_INFO, info(gid, acc, all_=False))
    m = getattr(client, "missions", None)
    if m is not None:
        mission_progress(m, req.score)
        if m.changed_tasks:
            m.save()
            acc.save()
            await client.send_packet(0x0E02, m.change_message())
    await client.send_packet(MSG_RESULT, P.ArderResultS2C(game_id=req.game_id, score=req.score,
                                                          max_score=best).SerializeToString())


HANDLERS = {MSG_RESULT: handle_result}
