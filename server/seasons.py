"""
Season ends and daily rank rewards, as mail (mail.py). Run once at server start and every minute.

  Arena ("Peak Challenge" in the game): every day at 21:00 (arena row 12) each ranked player gets
      the arenareward band's mail (templates 101..133, rewards in the template; captured: daily
      mails "785127290" with 112 arena coins). A new arena season clears the season chests and
      the season-best score; the score itself carries on (captured season 15 started at ~5000).
  Triple Clash: when its 30-day season (peak.season) changes, every player of the old season gets
      template 284 ("You ranked #{0} ...", args [rank, date]) with the arenareward3v3 band's Reward;
      ladder chests and the season best start again, the score carries on (no captured reset rule).
      The rank's TimeLimitedReward (frame, chat bubble, title) is unlocked until the new season's
      end (personal.py), as the captured owner account had it.
  Freedom Championship: when a freedomchampionshipconf season has closed, every player of it gets
      template 320 ("ranking #{0}") with freedomrankrewardconf (FreedomId, Score = rank range), and
      template 319 with the season rank chests (freedomrankconf Reward) reached but not claimed.
Done jobs are remembered in pvp.json ("season_jobs"), so a restart never sends a mail twice.
"""
import asyncio
import logging
import time

import mail
import personal
import pvp

log = logging.getLogger("hfh")

ARENA_REWARD = pvp._cfg("arenareward")
PEAK_REWARD = pvp._cfg("arenareward3v3")
FREEDOM_REWARD = pvp._cfg("freedomrankrewardconf")
DAILY_HOUR = 21  # arena row 12: "21:00:00"


def _jobs() -> dict:
    return pvp.state().setdefault("season_jobs", {})


def _band(rows, rank: int, key="Rank"):
    return next((r for r in rows if r[key][0] <= rank <= r[key][1]), None)


def _ranked(entries: dict) -> list[tuple[int, str]]:
    order = sorted(entries.items(), key=lambda kv: (-kv[1].get("score", 0), kv[0]))
    return [(i + 1, g) for i, (g, _) in enumerate(order)]


def arena_daily(now: float) -> int:
    """Daily arena rank mail at 21:00 (local server time), once per day."""
    jobs = _jobs()
    lt = time.localtime(now)
    today = time.strftime("%Y-%m-%d", lt)
    if lt.tm_hour < DAILY_HOUR or jobs.get("arena_daily") == today:
        return 0
    sent = 0
    for rank, gid in _ranked(pvp.state().get("arena", {})):
        row = _band(ARENA_REWARD, rank)
        if row and row.get("Mail"):
            mail.send(gid, mail.make(row["Mail"], args=[rank], now=now))
            sent += 1
    jobs["arena_daily"] = today
    return sent


def arena_season(now: float) -> bool:
    jobs = _jobs()
    sid = pvp.season(now)[0]
    if jobs.get("arena_season") == sid:
        return False
    first = "arena_season" not in jobs
    jobs["arena_season"] = sid
    if first:
        return False
    for d in pvp.state().get("arena", {}).values():
        d["history"], d["gifts"] = d.get("score", 0), []
    return True


def peak_season(now: float) -> int:
    import peak
    jobs = _jobs()
    sid, _, end, _ = peak.season(now)
    done = jobs.get("peak_season")
    jobs["peak_season"] = sid
    if done is None or done == sid:
        return 0
    entries = pvp.state().get("peak", {})
    until = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end))
    sent = 0
    for rank, gid in _ranked(entries):
        row = _band(PEAK_REWARD, rank)
        if row:
            mail.send(gid, mail.make(284, items=mail.items_of(row.get("Reward")), args=[rank, until], now=now))
            # frame / chat bubble / title of the rank, active until the end of the new season
            personal.grant(gid, row.get("TimeLimitedReward") or [], end)
            sent += 1
    for d in entries.values():  # new season: chests and season best start again
        d["ladder"], d["max"] = 0, d.get("score", 0)
        d["records"] = []
    return sent


def freedom_seasons(now: float) -> int:
    import freedom
    jobs = _jobs()
    done = set(jobs.get("freedom_done", []))
    entries = pvp.state().get("freedom", {})
    sent = 0
    for s in freedom.SEASONS:
        sid = s["ID"]
        if sid in done or freedom._time(s["CloseTime"]) > now:
            continue
        players = {g: d for g, d in entries.items() if d.get("season") == sid}
        for rank, gid in _ranked(players):
            row = next((r for r in FREEDOM_REWARD if r.get("FreedomId") == sid
                        and r["Score"][0] <= rank <= r["Score"][1]), None)
            if row:
                mail.send(gid, mail.make(320, items=mail.items_of(row.get("Reward")), args=[rank], now=now))
                sent += 1
            d = players[gid]
            missed = []
            for band in freedom.RANKS:
                if band.get("Reward") and band["ID"] not in d.get("gifts", []) \
                        and d.get("history", 0) >= band["Score"][0]:
                    missed += mail.items_of(band["Reward"])
                    d.setdefault("gifts", []).append(band["ID"])
            if missed:
                mail.send(gid, mail.make(319, items=missed, now=now))
                sent += 1
        done.add(sid)
    jobs["freedom_done"] = sorted(done)
    return sent


def run(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    out = {"arena_daily": arena_daily(now), "arena_season": arena_season(now),
           "peak": peak_season(now), "freedom": freedom_seasons(now)}
    pvp.save()
    if any(out.values()):
        log.info(f"  SEASONS {out}")
    return out


async def loop(every: float = 60.0):
    while True:
        try:
            run()
        except Exception as e:  # never stop the server for this
            log.error(f"  SEASONS failed: {e!r}")
        await asyncio.sleep(every)
