"""
Real-time PvP, stage 1: two players who search for a Freedom Championship match at the same time
are paired and play one live match together: both clients watch the same rallies at the same
time, each from its own side.

The client always plays as the home team (the game never had real-time PvP). So the server builds
every rally once, from player 1's point of view (one shared livematch.LiveMatch), and sends
player 2 a mirrored copy: player ids 0..12 <-> 13..25, team ids 0 <-> 1, host/guest scores and
flags swapped, world positions turned by 180 degrees around the net (x, z negated). Each client
gets its own order counter and reads the rally list at its own pace.

Stage 1 has no player inputs: ultimates are released by the AI for both teams (auto_skill), hand
serve / ultimate / morale requests are answered with the unchanged rally. Stage 2 (inputs for both
teams) needs engine changes, see docs/realtime-pvp.md. Done of stage 2 without the engine:
substitutions (0x0309) of both players: player 1's through LiveMatch.formation_change, player 2's
as the away ids 13..25 (the game sends them so) and checked the same way; both go into the shared LiveMatch's
pending_subs and take effect from the next rally the room builds, for both viewers. Leaving a running
match counts as a loss for the one who left (freedom.on_disconnect); the other plays on.
Shared tempo: rally n goes out when both clients have asked for it (a barrier, wait_turn): the first
to ask waits (at most TEMPO_WAIT seconds; for the first rally also until the other has joined the
room), the second one's request releases both, so both watch the same rally at the same time; no waiting once the other has left or its match
is over. server.handle_match_data waits in a task, so the connection keeps answering heartbeats.

Matchmaking: freedom.handle_match calls pair(); a searching player waits up to WAIT seconds for a
second searcher (same pattern), otherwise freedom falls back to a saved opponent as before.
"""
import asyncio
import logging
import random
import time

import matches

P = matches.P
log = logging.getLogger(__name__)

WAIT = 12.0  # seconds a searcher waits for a second player
TEMPO_WAIT = 25.0  # seconds a client waits for the other one (the game shows a rally for 8-16 s)
TEAM_IDS = 13
_waiting: dict[int, tuple] = {}  # pattern -> (client, future)
ROOMS: dict[str, "Room"] = {}


# --- mirroring ------------------------------------------------------------------------------------
def _pid(i: int) -> int:
    if 0 <= i < TEAM_IDS:
        return i + TEAM_IDS
    if TEAM_IDS <= i < 2 * TEAM_IDS:
        return i - TEAM_IDS
    return i


def _team(t: int) -> int:
    return 1 - t if t in (0, 1) else t


def _neg(v):
    """Turn a world vector 180 degrees around the net (x, z negated); zeros stay unset."""
    if v.x:
        v.x = -v.x
    if v.z:
        v.z = -v.z


def _vec(parent, name: str):
    if parent.HasField(name):
        _neg(getattr(parent, name))


def _curve(c):
    if c.HasField("fly_curve_data"):
        f = c.fly_curve_data
        _vec(f, "start_velocity3_d")
        if f.HasField("plane_to_world"):
            m = f.plane_to_world
            for col in ("c0", "c1", "c2", "c3"):
                _vec(m, col)
    if c.HasField("move_curve_data"):
        mv = c.move_curve_data
        if mv.HasField("bezier"):
            for v in ("p0", "p1", "c0"):
                _vec(mv.bezier, v)
        _vec(mv, "target_pos")
        _vec(mv, "end_forward")


def _hit(h):
    h.hit_player = _pid(h.hit_player)
    _vec(h, "hit_ball_pos")
    h.total_power_host, h.total_power_guest = h.total_power_guest, h.total_power_host


def _score(s):
    s.host_score, s.guest_score = s.guest_score, s.host_score
    s.score_team = _team(s.score_team)


def _member(m):
    if m.entity_type == 1:  # EntityType_TEAM
        m.entity_id = _team(m.entity_id)
    else:
        m.entity_id = _pid(m.entity_id)


def mirror_frame(raw: bytes) -> bytes:
    f = P.FrameData.FromString(raw)
    if f.HasField("ball_data"):
        _vec(f.ball_data, "position")
        if f.ball_data.HasField("curve_data"):
            _curve(f.ball_data.curve_data)
    for p in f.player_data:
        p.player_id = _pid(p.player_id)
        _vec(p, "position")
        _vec(p, "forward")
        if p.HasField("curve_data"):
            _curve(p.curve_data)
    for t in f.team_data:
        t.team_id = _team(t.team_id)
        if t.HasField("subs"):
            on, off = [_pid(i) for i in t.subs.on_stage], [_pid(i) for i in t.subs.off_stage]
            del t.subs.on_stage[:], t.subs.off_stage[:]
            t.subs.on_stage.extend(on)
            t.subs.off_stage.extend(off)
        if t.HasField("team_stance_data"):
            st = [_pid(i) for i in t.team_stance_data.stance_info]
            del t.team_stance_data.stance_info[:]
            t.team_stance_data.stance_info.extend(st)
        if t.HasField("team_side_data"):
            sd = t.team_side_data
            sd.left_team_id, sd.right_team_id = _team(sd.left_team_id), _team(sd.right_team_id)
    if f.HasField("round_data"):
        r = f.round_data
        r.serve_team = _team(r.serve_team)
        if r.HasField("action_data"):
            _hit(r.action_data)
        if r.HasField("hit_data"):
            _hit(r.hit_data)
    for parent, point in ((f.game_data, "game_point"), (f.match_data, "match_point")):
        if parent is f.game_data and not f.HasField("game_data"):
            continue
        if parent is f.match_data and not f.HasField("match_data"):
            continue
        if parent.HasField("score"):
            _score(parent.score)
        if parent.HasField(point):
            pt = getattr(parent, point)
            pt.is_host, pt.is_guest = pt.is_guest, pt.is_host
    for c in f.property_data.changed_property:
        if c.player_id == -2147483648:  # a team entry
            c.team_id = _team(c.team_id)
        else:
            c.player_id = _pid(c.player_id)
    sk = f.skill_data
    for group in (sk.ultimate_skills, sk.casting_skills, sk.team_skills, sk.deactived_ultimate_skills,
                  sk.deactived_team_skills, sk.casted_team_skills, sk.changed_skills, sk.record_cd_info_skills):
        for s in group:
            if s.is_team:
                s.player_id = _team(s.player_id)
            else:
                s.player_id = _pid(s.player_id)
    for s in sk.failed_skill_infos:
        s.player_id = _pid(s.player_id)
    for b in f.buff_data.changed_buffs:
        _member(b.target)
    for b in f.buff_modified_data:
        _member(b.team_member)
    if f.HasField("power_data"):
        pd = f.power_data
        pd.home_power, pd.oppoent_power = pd.oppoent_power, pd.home_power
    for h in f.power_hit_ball_data:
        h.hit_player = _pid(h.hit_player)
    return f.SerializeToString()


def mirror_info(info: P.PVPRoomInfo) -> P.PVPRoomInfo:
    out = P.PVPRoomInfo()
    out.CopyFrom(info)
    out.home.CopyFrom(info.away)
    out.away.CopyFrom(info.home)
    out.first_serve_team = not info.first_serve_team
    return out


# --- shared room ----------------------------------------------------------------------------------
class Room:
    """One live match for two clients. Rallies are built once (player 1's view) and stored."""

    def __init__(self, key: str, info: P.PVPRoomInfo, gids: tuple[int, int]):
        import livematch
        self.key, self.info, self.gids = key, info, gids  # gids[0] = host side
        self.live = livematch.LiveMatch(info, matches.point_chance(info.home.power, info.away.power))
        self.live.auto_skill = True  # no user inputs in stage 1: the AI releases both teams' ultimates
        self.rallies: list[list[bytes]] = []
        self.after: list[tuple[list, bool]] = []  # (score, over) after each rally
        self.created = time.time()
        self.views: dict[int, "SideView"] = {}  # side -> the client's view (shared tempo)
        self.moved = asyncio.Event()  # set whenever a view is sent a rally

    def rally(self, n: int) -> tuple[list[bytes], list, bool]:
        while len(self.rallies) <= n and not (self.after and self.after[-1][1]):
            frames = self.live.next_rally()
            self.rallies.append(frames)
            self.after.append((list(self.live.score), self.live.over))
        n = min(n, len(self.rallies) - 1)
        return self.rallies[n], self.after[n][0], self.after[n][1]

    def side(self, gid: int) -> int:
        return 0 if gid == self.gids[0] else 1


class SideView:
    """What server.py's live-match handlers use (the LiveMatch interface), for one of the clients."""

    def __init__(self, room: Room, side: int):
        self.room, self.side = room, side
        self.info = room.info if side == 0 else mirror_info(room.info)
        self.order = 0
        self.n = 0  # rallies sent
        self.last = None
        self.over = False
        self.score = [0, 0]
        self.rally_no = 0
        self.inputs: dict = {}
        self.input_note = None
        self.win_chance = room.live.win_chance if side == 0 else 1 - room.live.win_chance
        self.auto_skill = True
        self.pending_subs: list = []
        self.subs_left = {0: 0, 1: 0}
        self.gone = False  # the client left the match (disconnected)
        self.want = -1  # the rally this client asked for last (shared tempo)

    @property
    def won(self) -> bool:
        return self.score[0] > self.score[1]

    @property
    def played(self) -> dict:
        """Rallies this client was sent (its own view), for the result statistics (matches.live_statistics)."""
        return {i + 1: (self._view(r), None) for i, r in enumerate(self.room.rallies[:self.n])}

    def _view(self, frames: list[bytes]) -> list[bytes]:
        return frames if self.side == 0 else [mirror_frame(f) for f in frames]

    async def wait_turn(self):
        """Shared tempo: rally n is sent when the other client asks for it too (or was sent it)."""
        end = time.time() + TEMPO_WAIT
        self.want = self.n
        self.room.moved.set()  # a waiting partner sees this request
        while True:
            other = self.room.views.get(1 - self.side)
            if other is not None and (other.gone or other.over or other.want >= self.n or other.n > self.n):
                return
            left = end - time.time()
            if left <= 0:
                return
            self.room.moved.clear()
            try:
                await asyncio.wait_for(self.room.moved.wait(), left)
            except asyncio.TimeoutError:
                return

    def next_rally(self) -> list[bytes]:
        frames, score, over = self.room.rally(self.n)
        self.n += 1
        self.room.moved.set()
        self.rally_no = self.n
        self.score = list(score) if self.side == 0 else [score[1], score[0]]
        self.over = over
        self.last = self._view(frames)
        return self.last

    def reply(self, frames: list[bytes]) -> bytes:
        msg = P.PVPMatchDataS2C(session=self.info.session, order=self.order, start_time=int(time.time()))
        msg.frames.extend(frames)
        self.order += 1
        return msg.SerializeToString()

    def apply_input(self, msg_id: int, body: bytes) -> list[bytes]:
        self.input_note = "real-time match: inputs are not used yet (stage 1)"
        return self.last

    def formation_change(self, body: bytes) -> bytes:
        """0x0309 of this client: the shared match's substitutions for this client's team."""
        live = self.room.live
        if self.side == 0:
            rep = P.PVPFormationChangeS2C.FromString(live.formation_change(body))
        else:
            rep = P.PVPFormationChangeS2C()
            req = P.PVPFormationChangeC2S.FromString(body)
            team = 1  # GUEST
            court = set(live.stance[team]) | {on for t, on, off in live.pending_subs if t == team}
            log.info(f"  RT guest substitution request on {list(req.subs.on_stage)} off {list(req.subs.off_stage)}, "
                     f"court {sorted(court)}, valid {sorted(live.valid)}, libero {live.libero[team]}")
            for on, off in zip(req.subs.on_stage, req.subs.off_stage):
                # the game sends the away ids 13..25 here (seen: on 20 / off 13); 0..12 is mapped too
                g_on, g_off = (on if on >= TEAM_IDS else on + TEAM_IDS), (off if off >= TEAM_IDS else off + TEAM_IDS)
                ok = (live.subs_left[team] > 0 and g_on < 2 * TEAM_IDS and g_off < 2 * TEAM_IDS and g_on in live.valid
                      and g_on not in court and g_on != live.libero[team] and g_off in court)
                if not ok:
                    continue
                live.pending_subs = [s for s in live.pending_subs if s[1] != g_off]
                live.pending_subs.append((team, g_on, g_off))
                court = (court - {g_off}) | {g_on}
                live.subs_left[team] -= 1
                rep.subs.on_stage.append(on)
                rep.subs.off_stage.append(off)
        rep.session, rep.order = self.info.session, self.order
        self.order += 1
        self.subs_left = {0: live.subs_left[self.side], 1: live.subs_left[1 - self.side]}
        self.pending_subs = list(live.pending_subs)  # for the log line in server.py
        return rep.SerializeToString()


# --- matchmaking ----------------------------------------------------------------------------------
def _connected(client) -> bool:
    w = getattr(client, "writer", None)
    return w is None or not w.is_closing()


async def pair(client, pattern: int) -> tuple | None:
    """Wait up to WAIT seconds for a second searcher. Returns (room key, other client, host gid) or None."""
    other = _waiting.get(pattern)
    if other and other[0].gid != client.gid and _connected(other[0]) and not other[1].done():
        _waiting.pop(pattern, None)
        key = f"rt-{int(time.time() * 1000)}-{random.getrandbits(16)}"
        other[1].set_result((key, client, other[0].gid))
        return key, other[0], other[0].gid
    fut = asyncio.get_running_loop().create_future()
    _waiting[pattern] = (client, fut)
    try:
        return await asyncio.wait_for(asyncio.shield(fut), WAIT)
    except asyncio.TimeoutError:
        return None
    finally:
        if _waiting.get(pattern, (None, None))[1] is fut:
            _waiting.pop(pattern, None)


def create_room(key: str, host_team: P.PVPTeamInfo, guest_team: P.PVPTeamInfo, gids: tuple[int, int]) -> Room:
    if key in ROOMS:
        return ROOMS[key]
    now = int(time.time() * 1000)
    create = matches._captured("0x0302", P.PVPCreateS2C)
    info = create.info
    info.room_args.CopyFrom(P.PVPRoomArgs(pvp_type=P.FIGHT_FIGHTFREEDOM, pvp_args=[0]))
    info.home.CopyFrom(host_team)
    info.away.CopyFrom(guest_team)
    for t in (info.home, info.away):
        t.is_robot = False
        while len(t.formation.players) < TEAM_IDS:
            t.formation.players.add()
    info.preset_match_id = 0
    info.bo_type, info.bo_score, info.time_end = 1, matches.LIVE_POINTS, 0
    info.first_serve_team = False
    info.create_time, info.session, info.seed = now // 1000, now, random.getrandbits(31)
    room = ROOMS[key] = Room(key, info, gids)
    for k in [k for k, r in ROOMS.items() if time.time() - r.created > 3600]:  # old rooms
        ROOMS.pop(k, None)
    return room


def join(key: str, gid: int) -> tuple[bytes, SideView] | None:
    room = ROOMS.get(key)
    if room is None or gid not in room.gids:
        return None
    view = SideView(room, room.side(gid))
    room.views[view.side] = view
    room.moved.set()  # a client waiting for its partner to join goes on
    create = matches._captured("0x0302", P.PVPCreateS2C)
    create.info.CopyFrom(view.info)
    return create.SerializeToString(), view
