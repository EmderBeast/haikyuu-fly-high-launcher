"""Result message statistics (0x0304 PVPBattleResult.result) of a server-streamed match: counted from the
rallies the client was sent, as Garena does, not copied from the captured match.

1. Offline match (temporary accounts, no port): a Specialty Trial match played to the end with hand serves;
   the result's scores, rally count, touches, point-winning touches, hand serves and line-up must agree with
   the frames the client got.
2. If the Garena captures are on this PC (HFH_CAPTURES, default D:\\2nd Brain\\Haikyu-Fly-High-Analyse\\capture),
   matches.live_statistics is run on every complete captured match and compared with Garena's own result.

  python test_result_statistics.py
"""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-resstat-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
CAPTURES = Path(os.environ.get("HFH_CAPTURES", r"D:\2nd Brain\Haikyu-Fly-High-Analyse\capture"))
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent = gid, []

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


def frames_of(payload, cls):
    m = cls()
    m.ParseFromString(payload)
    return [P.FrameData.FromString(f) for f in m.frames]


async def offline():
    c = FakeClient(990611)
    await server.handle_login(c, wire.build([(1, 0, 990611), (7, 2, b"token")]))
    req = P.PVPCreateC2S(args=P.PVPRoomArgs(pvp_type=P.FIGHT_SPECIFIC_TRIAL, pvp_args=[0, 320101]))
    await server.handle_pvp_create(c, req.SerializeToString())
    room = c.room
    live = room.live
    seen = []  # final version of every rally as the client got it, with the hand serve result
    hand_results = [2, 1, 3]
    for _ in range(60):
        if live.over:
            break
        await server.handle_match_data(c, P.PVPMatchDataC2S(session=live.info.session, order=live.order).SerializeToString())
        fr = frames_of(c.last(0x0303), P.PVPMatchDataS2C)
        hand = None
        if any(f.round_data.HasField("hit_data") and f.round_data.hit_data.formula_type == 1
               and f.round_data.hit_data.hit_player < 13 for f in fr):
            hand = hand_results[len(seen) % 3]
            body = P.PVPHandleServeC2S(s_index=77, result=hand).SerializeToString()
            await server.handle_match_input(c, body, 0x0307)
            fr = frames_of(c.last(0x0307), P.PVPHandleServeS2C)
        seen.append((fr, hand))
    check("match played to the end", live.over, f"{len(seen)} rallies, score {live.score}")
    await server.handle_pvp_result(c, P.PVPResultC2S().SerializeToString())
    res = P.PVPResultS2C()
    res.ParseFromString(c.last(0x0304))
    r = res.battle_result.result

    # what the frames say
    g0, acts, wins, prev, last_ids = 0, [], [], (0, 0), set()
    for n, (fr, hand) in enumerate(seen):
        for f in fr:
            if f.round_data.HasField("action_data"):
                acts.append((g0 + f.frame_index, f.round_data.action_data, n, hand))
            if f.HasField("game_data"):
                s = (f.game_data.score.host_score, f.game_data.score.guest_score)
                wins.append(0 if s[0] > prev[0] else 1)
                prev = s
            if n == len(seen) - 1:
                last_ids.update(p.player_id for p in f.player_data)
        g0 += fr[-1].frame_index
    home_bo, away_bo = r.home.bo_statistics[0], r.away.bo_statistics[0]
    check("scores = points won in the rallies", (home_bo.score, away_bo.score) == (wins.count(0), wins.count(1))
          and [home_bo.score, away_bo.score] == live.score, f"{home_bo.score}:{away_bo.score}")
    check("round_num = rallies played", home_bo.round_num == away_bo.round_num == len(seen), str(home_bo.round_num))
    check("win flags", home_bo.win == (home_bo.score > away_bo.score) and away_bo.win == (not home_bo.win)
          and r.home.bo_win_count == int(home_bo.win))
    lineup = [p.info.inst_id for p in room.info.home.formation.players if p.info.inst_id]
    check("home statistics: one per line-up card, in order", [s.inst_id for s in home_bo.player_statistics] == lineup)
    pid_of = {p.info.inst_id: i for i, p in enumerate(room.info.home.formation.players)}
    pid_of.update({p.info.inst_id: 13 + i for i, p in enumerate(room.info.away.formation.players)})
    stat_touches, total, wins_marked, serves = [], 0, 0, []
    for team, bo in ((0, home_bo), (1, away_bo)):
        for st in bo.player_statistics:
            pid = pid_of[st.inst_id]
            for s in st.formula_statistics:
                stat_touches.append((s.frame_idx, pid))
                total += 1
                wins_marked += s.skill_win
            check_score = [s.skill_id for s in st.formula_statistics if s.skill_win] == list(st.score_skills)
            if not check_score:
                check(f"score_skills of {pid} = its point-winning touches", False)
            serves += [(pid, b) for b in st.serve_balls]
            if st.on_stage != (pid in last_ids):
                check(f"on_stage of {pid}", False)
    want = sorted((g, a.hit_player) for g, a, _, _ in acts if a.hit_player in pid_of.values())
    check("every announced touch is in the statistics, at its global frame", sorted(stat_touches) == want,
          f"{total} touches")
    check("one point-winning touch per point with an own touch", 0 < wins_marked <= len(seen), f"{wins_marked}")
    want_serves = sorted((a.hit_player, hand) for g, a, n, hand in acts if hand and a.formula_type == 1)
    check("serve_balls = hand serve results of the server", sorted(serves) == want_serves and bool(want_serves),
          f"{len(want_serves)} hand serves")
    check("score_act_statistics: one per point won", len(home_bo.score_act_statistics) == home_bo.score
          and len(away_bo.score_act_statistics) == away_bo.score)
    check("morale_max 120", r.home.morale_max == r.away.morale_max == 120)


def captured():
    if not CAPTURES.is_dir():
        print(f"skip capture comparison ({CAPTURES} not found)")
        return
    cls = {0x0303: P.PVPMatchDataS2C, 0x0307: P.PVPHandleServeS2C, 0x030A: P.PVPUltimateSkillS2C,
           0x030B: P.PVPMoraleSkillS2C}
    n = same = 0
    fields = {}
    for folder in sorted(CAPTURES.glob("decrypted_*")):
        room, versions, hands = None, {}, set()
        for f in sorted(folder.glob("*.bin"), key=lambda p: int(p.stem.split("_")[0])):
            parts = f.stem.split("_")
            if len(parts) != 3:
                continue
            mid = int(parts[1]) << 8 | int(parts[2])
            raw = f.read_bytes()
            body = raw[1:] if raw[:1] == b"\x00" else raw
            try:
                if mid == 0x0302:
                    m = P.PVPCreateS2C.FromString(body)
                    if m.info.home.formation.players:
                        room, versions, hands = m.info, {}, set()
                elif mid in cls and room is not None:
                    fr = cls[mid].FromString(body).frames
                    if fr:
                        k = (P.FrameData.FromString(fr[0]).game_id, P.FrameData.FromString(fr[0]).round_id)
                        versions[k] = list(fr)
                        if mid == 0x0307:
                            hands.add(k)
                elif mid == 0x0304 and room is not None:
                    m = P.PVPResultS2C.FromString(body)
                    if not m.HasField("battle_result"):
                        continue
                    ks = sorted(versions)
                    if len(ks) >= 3 and ks[0][1] == 1 and len(ks) == ks[-1][1]:  # complete match
                        garena = m.battle_result.result
                        mine = P.PVPRoomResult()
                        mine.CopyFrom(garena)
                        # hand serve results: every captured one was 2 (Nor) except one; use Garena's own list
                        played = {i + 1: (versions[k], 2 if k in hands else None) for i, k in enumerate(ks)}
                        matches.live_statistics(mine, room, played)
                        n += 1
                        ok = True
                        for side_g, side_m in ((garena.home, mine.home), (garena.away, mine.away)):
                            bg, bm = side_g.bo_statistics[0], side_m.bo_statistics[0]
                            for name, a, b in (
                                    ("score", bg.score, bm.score), ("round_num", bg.round_num, bm.round_num),
                                    ("win", bg.win, bm.win), ("morale_num", side_g.morale_num, side_m.morale_num),
                                    ("score_act", [str(x) for x in bg.score_act_statistics],
                                     [str(x) for x in bm.score_act_statistics]),
                                    ("formula_statistics", [[str(x) for x in s.formula_statistics] for s in bg.player_statistics],
                                     [[str(x) for x in s.formula_statistics] for s in bm.player_statistics]),
                                    ("score_skills", [list(s.score_skills) for s in bg.player_statistics],
                                     [list(s.score_skills) for s in bm.player_statistics]),
                                    ("on_stage", [s.on_stage for s in bg.player_statistics],
                                     [s.on_stage for s in bm.player_statistics]),
                                    ("formidable", [(s.formidable_attack, s.formidable_defend) for s in bg.player_statistics],
                                     [(s.formidable_attack, s.formidable_defend) for s in bm.player_statistics]),
                                    ("serve_balls count", [len(s.serve_balls) for s in bg.player_statistics],
                                     [len(s.serve_balls) for s in bm.player_statistics])):
                                hit = a == b
                                fields.setdefault(name, [0, 0])[hit] += 1
                                ok &= hit
                        same += ok
                    room = None
            except Exception:
                continue
    print(f"captured matches compared: {n}, all fields equal in {same}")
    for name, (bad, good) in sorted(fields.items()):
        print(f"     {name:20s} {good}/{good + bad} sides equal")
    check("captures: score, rallies, win, morale releases, score acts equal everywhere",
          n >= 90 and all(fields[k][0] == 0 for k in ("score", "round_num", "win", "morale_num", "score_act")))
    # 8 of 188 sides differ in one skill_win flag of a failed touch (result 0), see frame_format.md 3.1
    check("captures: touch statistics equal in >= 95 % of the sides",
          fields["formula_statistics"][1] >= 0.95 * sum(fields["formula_statistics"]))
    check("captures: point-winning skills, formidable equal in >= 97 %",
          all(fields[k][1] >= 0.97 * sum(fields[k]) for k in ("score_skills", "formidable", "on_stage")))


if __name__ == "__main__":
    try:
        asyncio.run(offline())
        captured()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    raise SystemExit(0 if all(checks) else 1)
