"""
Server-streamed matches (no preset): the client asks for each rally with 0x0303 PVPMatchData and
renders the FrameData the server sends (decoded/analysis/frame_format.md).

Stage 1 of the match engine: every rally is a real Garena rally from the capture (its final
version), picked by serving team and point winner and patched into this match the same way the
client patches recorded rallies for preset matches (ids, scores, end flags, global curve frames).
Later stages replace the captured touches with simulated ones.
"""
import json
import random
import time
from pathlib import Path

import engine
import gameconfig
import matches
import rallygen
import skills

P = matches.P
MSGS = Path(__file__).parent.parent / "decoded" / "match_msgs.json"  # made by decoded/../dump of the capture
STORE = Path(__file__).parent / "data" / "rallies.bin"  # gzip: [first u8][n u16][len u32 frame]*n per rally
HOST, GUEST = 0, 1
JOB_MB = 2
SUBS_PER_SET = 4  # "Substitutions Left This Set: 4"
CLASSES = {"0x0303": P.PVPMatchDataS2C, "0x0307": P.PVPHandleServeS2C,
           "0x030A": P.PVPUltimateSkillS2C, "0x030B": P.PVPMoraleSkillS2C}


class Rally:
    def __init__(self, frames: list, first: bool):
        self.frames = frames  # FrameData, final version
        self.first = first  # captured as rally 1 (carries the match-start frames 1-7)
        self.serve = next((f.round_data.serve_team for f in frames
                           if f.HasField("round_data") and f.round_data.serve_team), HOST)
        end = [f for f in frames if f.HasField("game_data")]
        self.point = end[-1].game_data.score.score_team if end else HOST
        self.length = frames[-1].frame_index
        self.ids = _player_ids(frames)


def _player_ids(frames) -> set[int]:
    """Player ids (home 0..12, away 13..25) a rally refers to; all must exist in the room."""
    ids = set()
    for f in frames:
        ids.update(p.player_id for p in f.player_data)
        if f.HasField("round_data"):
            for h in (f.round_data.action_data, f.round_data.hit_data):
                if h.ByteSize():
                    ids.add(h.hit_player)
        for t in f.team_data:
            ids.update(t.subs.on_stage)
            ids.update(t.subs.off_stage)
            ids.update(t.team_stance_data.stance_info)
    return ids


def _load_library() -> list[Rally]:
    """Final version of every captured rally (last frame-carrying S2C per game/round)."""
    if not MSGS.exists():
        return []
    out = []
    versions = None
    for m in json.loads(MSGS.read_text()):
        if m["dir"] != "S->C":
            continue
        body = bytes.fromhex(m["hex"])
        body = body[1:] if body[:1] == b"\x00" else body
        if m["msg_id"] == "0x0302":
            if versions:
                out += [Rally(fr, key == (1, 1)) for key, fr in versions.items()]
            versions = {}
            continue
        if versions is None or m["msg_id"] not in CLASSES:
            continue
        x = CLASSES[m["msg_id"]]()
        try:
            x.ParseFromString(body)
        except Exception:
            continue
        frames = [P.FrameData.FromString(r) for r in x.frames]
        if frames:
            versions[(frames[0].game_id, frames[0].round_id)] = frames
    if versions:
        out += [Rally(fr, key == (1, 1)) for key, fr in versions.items()]
    return out


def _save(rallies: list[Rally]):
    import gzip
    import struct
    out = bytearray()
    for r in rallies:
        out += struct.pack("<BH", r.first, len(r.frames))
        for f in r.frames:
            raw = f.SerializeToString()
            out += struct.pack("<I", len(raw)) + raw
    STORE.write_bytes(gzip.compress(bytes(out)))


def _read() -> list[Rally]:
    import gzip
    import struct
    data, pos, out = gzip.decompress(STORE.read_bytes()), 0, []
    while pos < len(data):
        first, n = struct.unpack_from("<BH", data, pos)
        pos += 3
        frames = []
        for _ in range(n):
            (size,) = struct.unpack_from("<I", data, pos)
            frames.append(P.FrameData.FromString(data[pos + 4:pos + 4 + size]))
            pos += 4 + size
        out.append(Rally(frames, bool(first)))
    return out


def library() -> list[Rally]:
    if STORE.exists():
        return _read()
    rallies = _load_library()
    if rallies:
        _save(rallies)
    return rallies


LIBRARY = library()


def _shift_curves(msg, delta: int):
    """Add delta to every start_frame/end_frame (match-global numbering) inside msg."""
    for field, value in msg.ListFields():
        if field.type != field.TYPE_MESSAGE:
            if field.name in ("start_frame", "end_frame") and delta:
                setattr(msg, field.name, value + delta)
            continue
        if hasattr(value, "add"):  # repeated message field
            for v in value:
                _shift_curves(v, delta)
        else:
            _shift_curves(value, delta)


class LiveMatch:
    """State of one server-streamed match."""

    def __init__(self, info, win_chance: float):
        self.info = info
        self.win_chance = win_chance  # chance that home wins a point
        self.target = info.bo_score or 5
        self.score = [0, 0]
        self.serve = HOST if not info.first_serve_team else GUEST
        self.rally_no = 0
        self.global_frame = 0  # sum of the last frames of earlier rallies
        self.order = 0
        self.last = None  # frames of the current rally (resent on hand serve / skill inputs)
        self.over = False
        self.inputs: dict = {}  # user inputs for the current rally
        self.windows: list[tuple[int, int]] = []  # (ultimate window index, local frame) of the last build
        self.seed = 0
        self._before = None
        self.generate = True  # build rallies (engine + rallygen) instead of replaying captured ones
        self.stats = engine.stats_for(info)
        self.battle = skills.Battle(info, self.stats) if skills.AVAILABLE else None
        home = [i for i, p in enumerate(info.home.formation.players) if p.info.conf_id]
        away = [13 + i for i, p in enumerate(info.away.formation.players) if p.info.conf_id]
        self.stance = {HOST: home[:6], GUEST: away[:6]}
        self.libero = {HOST: home[6] if len(home) > 6 else None, GUEST: away[6] if len(away) > 6 else None}
        self.valid = ({i for i, p in enumerate(info.home.formation.players) if p.info.conf_id}
                      | {13 + i for i, p in enumerate(info.away.formation.players) if p.info.conf_id})
        self.jobs = {}
        for team, t in ((HOST, info.home), (GUEST, info.away)):
            for i, p in enumerate(t.formation.players):
                if p.info.conf_id:
                    self.jobs[team * 13 + i] = (gameconfig.PLAYERS.get(p.info.conf_id) or {}).get("Job")
        self.lib_for = {HOST: None, GUEST: None}  # player the libero is in for right now
        self.subs_left = {HOST: SUBS_PER_SET, GUEST: SUBS_PER_SET}
        self.pending_subs: list[tuple[int, int, int]] = []  # (team, on, off) for the next rally
        self.power_tot: dict[int, list[float]] = {}  # player -> [attack, defence] power this match

    @property
    def won(self) -> bool:
        return self.score[HOST] > self.score[GUEST]

    def _pick(self, point: int) -> Rally:
        first = self.rally_no == 1
        usable = [r for r in LIBRARY if r.first == first and r.ids <= self.valid]
        pool = ([r for r in usable if r.serve == self.serve and r.point == point]
                or [r for r in usable if r.point == point] or usable or LIBRARY)
        return random.choice(pool)

    def next_rally(self) -> list[bytes]:
        """Plays the next rally. Its starting state and random seed are kept so that a user input
        (hand serve, ultimate, morale skill) can re-simulate it, as Garena's server did."""
        self.rally_no += 1
        self.inputs = {}
        self.seed = random.getrandbits(32)
        for team, on, off in self.pending_subs:  # user substitutions take effect now
            base = self.stance[team]
            if off in base:
                base[base.index(off)] = on
            if self.lib_for[team] == off:  # the new player sits behind the libero instead
                self.lib_for[team] = on
        self.pending_subs = []
        self._before = (list(self.score), self.serve, {k: list(v) for k, v in self.stance.items()},
                        self.global_frame, self.battle.snapshot() if self.battle else None, dict(self.lib_for),
                        {k: list(v) for k, v in self.power_tot.items()})
        return self._play()

    def formation_change(self, body: bytes) -> bytes:
        """0x0309 PVPFormationChangeC2S: the user's substitutions, applied from the next rally
        ("Will take effect next round"); up to SUBS_PER_SET per set. Reply echoes the accepted ones."""
        req = P.PVPFormationChangeC2S.FromString(body)
        rep = P.PVPFormationChangeS2C(session=self.info.session, order=self.order)
        self.order += 1
        court = set(self.stance[HOST]) | {on for t, on, off in self.pending_subs if t == HOST}
        for on, off in zip(req.subs.on_stage, req.subs.off_stage):
            ok = (self.subs_left[HOST] > 0 and on in self.valid and on < 13 and off < 13
                  and on not in court and on != self.libero[HOST] and off in court)
            if not ok:
                continue
            self.pending_subs = [s for s in self.pending_subs if s[1] != off]  # a sub-in subbed out again
            self.pending_subs.append((HOST, on, off))
            court = (court - {off}) | {on}
            self.subs_left[HOST] -= 1
            rep.subs.on_stage.append(on)
            rep.subs.off_stage.append(off)
        return rep.SerializeToString()

    INT_MIN = -2147483648
    PLAYER_PROPS = list(range(0, 15)) + [100]  # basic stats, morale, stamina, crit stats, stamina limit
    TEAM_PROPS = (7, 133, 134, 131, 137, 139, 132)

    def _properties(self, f):
        """property_data: every player's current attributes (stamina 120, buffed stats) and each team's
        morale block, as Garena sent in the rally setup frame. Without it the client shows the raw card
        values (stamina 1200000, stats x10000)."""
        b = self.battle
        for pid in sorted(b.cards):
            pc = f.property_data.changed_property.add(player_id=pid, team_id=self.INT_MIN, buff_id=self.INT_MIN)
            for t in self.PLAYER_PROPS:
                if t == 8:
                    v = b.energy.get(pid, 120.0)
                elif t == 100:
                    v = 120.0
                elif t == 7:
                    v = 0.0
                else:
                    v = b.attr(pid, t)
                pc.attrs.add(type=t, val=v, base_val=b.stats.get(pid, {}).get(t, v) if t < 15 else v)
        for team in (HOST, GUEST):
            pc = f.property_data.changed_property.add(player_id=self.INT_MIN, team_id=team, buff_id=self.INT_MIN)
            vals = {7: b.morale[team], 133: 120.0, 134: 0.0, 131: 10000.0, 137: 0.0, 139: 0.0, 132: 0.0}
            for t in self.TEAM_PROPS:
                pc.attrs.add(type=t, val=vals[t], base_val=vals[t])

    def _libero(self, team: int) -> tuple[list[int], list[tuple[int, int, int]]]:
        """Libero rule: the libero takes the place of a middle blocker in the back row (slots 5/6, or
        slot 1 when the team receives) and leaves when that player rotates to the front or to serve.
        Returns the team's players on court and the swaps for frame 1 (team, on, off), as Garena sent."""
        lib, base = self.libero[team], self.stance[team]
        if lib is None:
            return list(base), []
        back = [5, 4] + ([0] if team != self.serve else [])
        cur = self.lib_for[team]
        eligible = [base[i] for i in back if self.jobs.get(base[i]) == JOB_MB]
        target = cur if cur in eligible else (eligible[0] if eligible else None)
        changes = []
        if cur != target:
            if cur is not None and cur in base:
                changes.append((team, cur, lib))
            if target is not None:
                changes.append((team, lib, target))
        self.lib_for[team] = target
        return [lib if p == target else p for p in base], changes

    def apply_input(self, msg_id: int, body: bytes) -> list[bytes]:
        """0x0307 hand serve / 0x030A ultimates / 0x030B morale skill: replay the rally with it."""
        if msg_id == 0x0307:
            m = P.PVPHandleServeC2S.FromString(body)
            self.inputs["hand"] = m.result or 2
        elif msg_id == 0x030A:
            m = P.PVPUltimateSkillC2S.FromString(body)
            frame = m.base.frame_index
            # the client names the window by its frame; a choice for a window that the current
            # version of the rally no longer has (an earlier choice changed it) is dropped
            # the client names the window by a frame next to it (captures: window 104, request 105; our
            # rallies: window 86, request 85): take the closest window within 4 frames
            near = [(abs(frame - f), i) for i, f in self.windows if abs(frame - f) <= 4]
            idx = min(near)[1] if near else None
            self.input_note = f"ult request frame {frame}, windows {self.windows} -> {idx}"
            if idx is not None:
                self.inputs.setdefault("ults", {})[idx] = [(v.player_id, v.skill_id) for v in m.values]
        elif msg_id == 0x030B:
            m = P.PVPMoraleSkillC2S.FromString(body)
            self.inputs["morale"] = m.hit_index
        return self._play()

    def _play(self) -> list[bytes]:
        score, serve, stance, gframe, snap, lib_for, power_tot = self._before
        self.power_tot = {k: list(v) for k, v in power_tot.items()}
        self.score, self.serve, self.global_frame = list(score), serve, gframe
        self.stance = {k: list(v) for k, v in stance.items()}
        self.lib_for = dict(lib_for)
        swaps, sent_stance = [], None
        if snap is not None:
            self.battle.restore(snap)
        point = HOST if random.random() < self.win_chance else GUEST
        rotated = None
        morale_at_start = bool(self.battle and self.battle.morale_ready(HOST))
        if self.generate:
            eff = {}
            for team in (HOST, GUEST):
                eff[team], changes = self._libero(team)
                swaps += changes
            court = rallygen.court_for(self.info, eff)
            rallygen.place(court, self.serve, {t: (l if l not in eff[t] else None) for t, l in self.libero.items()})
            start = dict(court.pos)
            behind = {p for p in self.lib_for.values() if p is not None}
            played = engine.simulate(court, self.serve, self.stats, rng=random.Random(self.seed),
                                     battle=self.battle, inputs=self.inputs, behind_libero=behind)
            point = played.winner
            for st in played.steps:  # attack / defence power of every touch, for the score display
                tot = self.power_tot.setdefault(st.player, [0.0, 0.0])
                tot[0 if st.formula in engine.ATTACK else 1] += st.power.ball
            touches = rallygen.touches_from(court, self.serve, played.steps, rng=random.Random(self.seed + 1))
            built, length = rallygen.build(court, self.serve, touches, self.global_frame, start,
                                           played.start, played.end)
            self.windows = [(t.window[0], t.window_frame) for t in touches if t.window]
            rally = Rally(built, False)
            rally.length = length
            shift = 0
            if point != self.serve:  # side-out: the team that wins the serve rotates
                self.stance[point] = self.stance[point][1:] + self.stance[point][:1]
                sent_stance = eff[point][1:] + eff[point][:1]  # as on court (libero included)
                rotated = point
        else:
            rally = self._pick(point)
            shift = self.global_frame - self._captured_offset(rally)
        self.score[point] += 1
        end = max(self.score) >= self.target
        out = []
        for src in rally.frames:
            f = P.FrameData()
            f.CopyFrom(src)
            f.game_id, f.round_id = 1, self.rally_no
            if f.HasField("round_data"):
                f.round_data.id = self.rally_no if f.round_data.id else 0
                f.round_data.serve_team = self.serve
            if f.HasField("skill_data") and not self.generate:  # no user choices on captured rallies
                for name in ("ultimate_skills", "failed_skill_infos", "team_skills"):
                    f.skill_data.ClearField(name)
            if f.frame_index == 1:  # libero swaps before the rally (frame_format.md 1.3)
                for team, on, off in swaps:
                    td = f.team_data.add()
                    td.team_id = team
                    td.subs.on_stage.append(on)
                    td.subs.off_stage.append(off)
            if f.frame_index == 2 and self.battle:  # property block like Garena's setup frame (shown values)
                self._properties(f)
            if morale_at_start and f.frame_index == 2:  # morale skill still available from before
                td = f.team_data.add()
                td.morale_skill.state, td.morale_skill.skill_id = P.Actived, 300
                f.skill_data.team_skills.add(skill_id=300, is_team=True)
            _shift_curves(f, shift)
            if f.HasField("game_data") and self.generate:
                # power_hit_ball_data: every player's summed attack / defence power, as Garena sent on each
                # rally's end frame (the club boss / score modes show it as the match score)
                for pid in sorted(self.valid):
                    a, d = self.power_tot.get(pid, (0.0, 0.0))
                    f.power_hit_ball_data.add(hit_player=pid, formidable_attack=int(a), formidable_defend=int(d))
            if f.HasField("game_data"):
                g = f.game_data
                g.id = 1
                g.score.host_score, g.score.guest_score = self.score
                g.score.score_team = point
                g.end = end
                if rotated is not None:
                    td = f.team_data.add()
                    td.team_id = rotated
                    td.team_stance_data.stance_info.extend(sent_stance or self.stance[rotated])
                f.ClearField("match_data")
                if end:
                    f.match_data.end = True
                    f.match_data.score.host_score = int(self.score[HOST] > self.score[GUEST])
                    f.match_data.score.guest_score = int(self.score[GUEST] > self.score[HOST])
                elif self.target - 1 in self.score:
                    f.match_data.match_point.is_host = self.score[HOST] == self.target - 1
                    f.match_data.match_point.is_guest = self.score[GUEST] == self.target - 1
            out.append(f.SerializeToString())
        self.global_frame += rally.length
        self.serve = point
        self.over = end
        self.last = out
        return out

    @staticmethod
    def _captured_offset(rally: Rally) -> int:
        """Global frame offset the rally had in its captured match (= start of its first ball curve
        minus that touch's local hit_frame)."""
        for f in rally.frames:
            if f.HasField("ball_data") and f.ball_data.curve_data.start_frame:
                hit = next((g.round_data.hit_data.hit_frame for g in rally.frames
                            if g.HasField("round_data") and g.round_data.HasField("hit_data")), None)
                if hit is not None:
                    return f.ball_data.curve_data.start_frame - hit
        return 0

    def reply(self, frames: list[bytes]) -> bytes:
        # start_time = server time of this rally (seconds); the client runs the countdown of timed
        # modes (club boss, Specialty Trial) from it, as from Garena's replies
        self.rally_start_time = int(time.time())
        msg = P.PVPMatchDataS2C(session=self.info.session, order=self.order, start_time=self.rally_start_time)
        msg.frames.extend(frames)
        self.order += 1
        return msg.SerializeToString()
