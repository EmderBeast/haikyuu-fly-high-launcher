"""Offline test of the card missions 61001 ("Win N matches of any type" for one card, taskdepotconfig
TaskType 20): +1 for a won match with the card in the home line-up, TaskChange (0x0E02) before the result
(0x0304) as in the Garena capture; nothing for a loss or without the card. Temporary accounts.
  python test_card_missions.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-cardmis-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"  # no Admin Mode for the temporary accounts
import matches  # noqa: E402
import missions  # noqa: E402
import wire  # noqa: E402

P = matches.P
TASK, CARD = 20510405, 5104  # captured: 61001, TastConditionTow [0, 5104], Value 120


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.writer = gid, [], None

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))


async def login(gid):
    c = FakeClient(gid)
    await server.handle_login(c, wire.build([(1, 0, gid), (7, 2, b"token")]))
    return c


def room(fight_type: int, cards: list[int]) -> P.PVPRoomInfo:
    info = P.PVPRoomInfo()
    info.room_args.pvp_type = fight_type
    for conf in cards:
        info.home.formation.players.add().info.conf_id = conf
    return info


def result(winner: int) -> bytes:
    out = P.PVPResultS2C()
    out.battle_result.result.winner = winner
    return out.SerializeToString()


def progress(acc) -> int:
    return wire.get(wire.parse(acc.task(TASK)), 3, 0)


async def main():
    cfg = missions._cfg(TASK)
    assert cfg.get("TaskType") == missions.CARD and cfg.get("TaskSubTargetType") == missions.CARD_WIN
    assert cfg.get("TastConditionTow") == [0, CARD], cfg.get("TastConditionTow")
    c = await login(9861)
    acc = c.account
    acc.tasks[TASK] = wire.build([(1, 0, TASK), (2, 0, 0), (3, 0, 35)])  # as in the capture before the match

    c.sent.clear()
    await server.before_result(c, result(1), room(4, [5502, 5501, 5302, 5702, 5701, CARD, 2513]))
    assert [m for m, _ in c.sent] == [server.MSG_TASK_CHANGE] and progress(acc) == 36
    print("win with card 5104 (fight type 4):", 35, "->", progress(acc))

    c.sent.clear()
    await server.before_result(c, result(2), room(2, [5502, 5501, 5302, 5702, 5701, CARD, 2513]))  # lost arena
    await server.before_result(c, result(1), room(3, [5502, 5501, 5302, 5702, 5701, 5503, 5101]))  # no card
    # other cards' missions may move (a new player has Garena's card missions of every character), 5104's not
    assert all(t.id != TASK for m, pb in c.sent if m == server.MSG_TASK_CHANGE
               for t in P.TaskChangeS2C.FromString(pb).tasks) and progress(acc) == 36
    bench = room(9, [5701, 1109, 5702, 2711, 5802, 1207, 1203] + [CARD])  # 5104 only on the bench (8th place)
    await server.before_result(c, result(1), bench)
    assert progress(acc) == 36
    print("loss / card missing / card on the bench: unchanged")

    # the target reached: finished (state TASK_FINISH), no further progress
    acc.tasks[TASK] = wire.build([(1, 0, TASK), (2, 0, 0), (3, 0, 119)])
    await server.before_result(c, result(1), room(9, [CARD]))
    assert progress(acc) == 120 and acc.task_state(TASK) == missions.TASK_FINISH
    await server.before_result(c, result(1), room(9, [CARD]))
    assert progress(acc) == 120

    # order on the real result path: TaskChange directly ahead of 0x0304
    acc.tasks[TASK] = wire.build([(1, 0, TASK), (2, 0, 0), (3, 0, 10)])
    info = matches._captured("0x0302", P.PVPCreateS2C).info
    info.home.formation.players[0].info.conf_id = CARD
    match = type("M", (), {"info": info, "won": True, "stars": 0b111, "live": None})()
    matches.roll_stage_rewards = lambda stage, first: []  # rewards are not what this checks
    c.sent.clear()
    await server.handle_preset_result(c, match)
    ids = [m for m, _ in c.sent]
    print("preset result messages:", [hex(m) for m in ids[:4]])
    assert server.MSG_TASK_CHANGE in ids and ids.index(server.MSG_TASK_CHANGE) == ids.index(0x0304) - 1
    assert progress(acc) == 11
    print("OK", TMP)


asyncio.run(main())
