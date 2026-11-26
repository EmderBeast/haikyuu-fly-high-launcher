"""
Daily/weekly missions: resets, progress from server-side events, activity points and chests.

Config (taskdepotconfig): TaskType 1 = daily, 6 = weekly. TaskSubTargetType is the
condition code, TastConditionTow its parameters, Value the target.
Rewards 107 / 114 are daily / weekly activity points (TaskActivation fields 2 / 3,
plus 1 = total), not currencies. Activity chests: dailyactivityreward (Type 1 daily, 6 weekly).

Wire: Task{1=id 2=state 3=progress}, TaskActivation{1=total 2=daily 3=week},
TaskActivationRewardState{1=id 2=state}; TaskChangeS2C{1=tasks 2=activation 3=chest states}.
"""
import struct
import time

import gameconfig
import wire
from accounts import TASK_FINISH

DAILY, WEEKLY = 1, 6
CARD = 20  # missions of one card (taskdepotconfig TaskType 20), e.g. "Win 120 matches of any type"
CARD_WIN = 61001  # TastConditionTow [fight type (0 = any), card conf id]
RESET_HOUR = 6  # "Resets Daily at 6:00 AM"
DAILY_POINTS, WEEKLY_POINTS = 107, 114
# Achievement gallery: the points of each achievement group are a currency of their own
# (AchievementGallery.AchievementGalleryViewUtils::GetCurrencyType: tab 8001..8004 -> 118, 119, 120, 121;
# the group's achievements pay exactly that currency in taskdepotconfig).
ACHIEVEMENT_CURRENCY = {8001: 118, 8002: 119, 8003: 120, 8004: 121}
CHEST_RECEIVED = 1

# condition codes
LOGIN = 10001
CONSUME_CURRENCY = 20001  # TastConditionTow = currency id
SPEND_CURRENCY = 80105  # TastConditionTow = [type, currency id]
FINISH_MATCH = 60001  # TastConditionTow = [fight type, stage or 0]
PEAK_MATCH = 50001
DAILY_MISSIONS_DONE = 99903
LEVEL_UP_CARD = 30001  # "Level up any player once" / "Level Up Players 5 Times"
TRAIN_CARD = 30006  # "Perform Player Training Once"
PROMOTE_CARD = 30003  # "Promote Any Player ..."
UPGRADE_SKILL = 30002  # "Upgrade any Player Skill ..."

def _float32(bits: int) -> float:
    return round(struct.unpack("<f", struct.pack("<I", bits))[0], 4)


def _cfg(task_id):
    return gameconfig.TASKS.get(task_id, {}) if gameconfig.AVAILABLE else {}


def periods(ts: float) -> dict:
    t = time.localtime(max(ts, 2 * 86400) - RESET_HOUR * 3600)  # Windows rejects negative timestamps
    year, week, _ = time.strftime("%G %V %u", t).split()
    return {"daily": time.strftime("%Y-%m-%d", t), "weekly": f"{year}-W{week}"}


class Missions:
    """Mission state of one account (stored inside the account file)."""

    def __init__(self, acc):
        self.acc = acc
        d = acc.missions or {}
        self.period = d.get("period") or periods(acc.b.sign["time"])  # baseline's last activity
        self.activation = d.get("activation") or dict(acc.b.activation)
        # stored stamina of the activity level (TaskActivation.physical_num, a float32), baseline's to start
        self.activation.setdefault("physical", _float32(wire.get(acc.b.activation_fields, 5, 0)))
        self.chests = {int(k): v for k, v in (d.get("chests") or acc.b.chests).items()}
        self.changed_tasks = []
        # achievement point rewards claimed by this account on top of the baseline's (TaskInfoS2C field 4)
        self.achievements = [int(i) for i in d.get("achievements") or []]
        self.new_achievements = []

    def save(self):
        self.acc.missions = {"period": self.period, "activation": self.activation,
                             "chests": {str(k): v for k, v in self.chests.items()},
                             "achievements": self.achievements}

    # --- achievement gallery -------------------------------------------------
    def achievements_received(self) -> set[int]:
        """Point rewards already taken: the baseline's achievement_rewards_state plus this account's."""
        got, data, pos = set(self.achievements), b"", 0
        for num, wt, v in wire.parse(self.acc.b.pb("0x0E01")):
            if num == 4:
                if wt == 2:
                    data += v
                else:
                    got.add(v)
        while pos < len(data):
            v, pos = wire.read_varint(data, pos)
            got.add(v)
        return got

    def claim_achievement(self, reward_id: int) -> list | None:
        """Point reward of the achievement gallery (achievementpointreward): claimable once when the
        group's points have reached its Point. The client checks the same before it asks
        (AchievementGalleryViewLogic::OnPointRewardEvent); the points are compared, not spent - the
        thresholds of a group only grow. Returns the rewards, None if it cannot be claimed."""
        row = gameconfig.ACHIEVEMENT_REWARDS.get(reward_id) if gameconfig.AVAILABLE else None
        currency = ACHIEVEMENT_CURRENCY.get((row or {}).get("TaskTeamId"))
        if not row or not currency or reward_id in self.achievements_received():
            return None
        if self.acc.get_currency(currency) < (row.get("Point") or 0):
            return None
        self.achievements.append(reward_id)
        self.new_achievements.append(reward_id)
        return gameconfig.open_rewards(gameconfig.parse_rewards(row.get("Reward")))

    # --- tasks -------------------------------------------------------------
    def _tasks_of(self, task_type):
        return [i for i in self.acc.all_task_ids() if _cfg(i).get("TaskType") == task_type]

    def _set(self, task_id, state, progress):
        f = wire.parse(self.acc.task(task_id))
        f = wire.set_varint(wire.set_varint(f, 2, state), 3, progress)
        self.acc.tasks[task_id] = wire.build(f)
        self.changed_tasks.append(task_id)

    def check_reset(self) -> bool:
        now = periods(time.time())
        changed = False
        for kind, task_type, field, chest_type in (("daily", DAILY, "daily", 1), ("weekly", WEEKLY, "week", 6)):
            if self.period[kind] == now[kind]:
                continue
            for tid in self._tasks_of(task_type):
                self._set(tid, 0, 0)
            self.activation[field] = 0
            for cid, row in gameconfig.ACTIVITY_CHESTS.items():
                if row.get("Type") == chest_type:
                    self.chests[cid] = 0
            self.period[kind] = now[kind]
            changed = True
        if changed:
            self.event(LOGIN, 1)
        return changed

    def event(self, code: int, amount: int = 1, params: tuple = ()):
        """Advance open daily/weekly missions with this condition code."""
        for task_type in (DAILY, WEEKLY):
            for tid in self._tasks_of(task_type):
                cfg = _cfg(tid)
                if cfg.get("TaskSubTargetType") != code or self.acc.task_state(tid) != 0:
                    continue
                if not _matches(code, cfg.get("TastConditionTow"), params):
                    continue
                progress = wire.get(wire.parse(self.acc.task(tid)), 3, 0) + amount
                target = cfg.get("Value") or 1
                self._set(tid, TASK_FINISH if progress >= target else 0, min(progress, target))

    def card_win(self, fight_type: int, cards: set[int]):
        """A won match: +1 on every open 61001 card mission whose card played in the home line-up.
        Garena capture (decoded_tshark.json with match_msgs.json, 6 results): wins with card 5104 in the line-up
        raised task 20510405 ([0, 5104]) by one each, a club boss win with 5104 and 2711 raised both cards'
        missions, a lost arena match with 5104 and wins without the cards changed nothing."""
        for tid in self._tasks_of(CARD):
            cfg = _cfg(tid)
            cond = cfg.get("TastConditionTow") or []
            if cfg.get("TaskSubTargetType") != CARD_WIN or self.acc.task_state(tid) != 0 or len(cond) < 2:
                continue
            if cond[0] not in (0, fight_type) or cond[1] not in cards:
                continue
            progress = wire.get(wire.parse(self.acc.task(tid)), 3, 0) + 1
            target = cfg.get("Value") or 1
            self._set(tid, TASK_FINISH if progress >= target else 0, min(progress, target))

    def on_claimed(self, task_id: int, rewards: list) -> list:
        """Book activity points of a claimed mission; returns the rewards that are real items."""
        rest = []
        for item, amount in rewards:
            if item == DAILY_POINTS:
                self.activation["daily"] += amount
                self.activation["total"] += amount
            elif item == WEEKLY_POINTS:
                self.activation["week"] += amount
            else:
                rest.append((item, amount))
        if _cfg(task_id).get("TaskType") == DAILY:
            self.event(DAILY_MISSIONS_DONE, 1)
        return rest

    def claim_chest(self, chest_id: int) -> list | None:
        row = gameconfig.ACTIVITY_CHESTS.get(chest_id) if gameconfig.AVAILABLE else None
        if not row or self.chests.get(chest_id) == CHEST_RECEIVED:
            return None
        have = self.activation["daily"] if row.get("Type") == 1 else self.activation["week"]
        if have < (row.get("Active") or 0):
            return None
        self.chests[chest_id] = CHEST_RECEIVED
        return gameconfig.open_rewards(gameconfig.parse_rewards(row.get("Reward")))

    def extract_physical(self, wanted: int, stamina: int, memory_max: int) -> int:
        """Take whole points out of the stored stamina (TaskActiveExtractPhysical): at most what is stored
        and what still fits below playerphysical.MemoryMax (ActiveLevelViewLogic::OnBtnGetEvent)."""
        n = max(0, min(wanted, int(self.activation["physical"]), memory_max - stamina))
        self.activation["physical"] -= n
        return n

    # --- wire --------------------------------------------------------------
    def change_message(self, all_tasks: bool = False) -> bytes:
        """TaskChangeS2C with changed tasks (or all account-changed ones), activation and chest states."""
        ids = list(self.acc.tasks) if all_tasks else list(dict.fromkeys(self.changed_tasks))
        self.changed_tasks = []
        out = [(1, 2, self.acc.task(i)) for i in ids]
        a = self.activation
        # keep the captured activity level / stamina bonus fields, change only the point counters
        act = self.acc.b.activation_fields
        for num, key in ((1, "total"), (2, "daily"), (3, "week")):
            act = wire.set_varint(act, num, a[key])
        bits = struct.unpack("<I", struct.pack("<f", a["physical"]))[0]
        act = sorted([f for f in act if f[0] != 5] + ([(5, 5, bits)] if a["physical"] else []), key=lambda f: f[0])
        out.append((2, 2, wire.build(act)))
        out += [(3, 2, wire.build([(1, 0, cid), (2, 0, st)])) for cid, st in sorted(self.chests.items())]
        # achievement_rewards_state (4, packed): the client adds these ids to the ones it knows
        # (AchievementGalleryMessageHandler::OnTaskChange), so only new ones are sent - at login all of
        # this account's, the baseline's are in the replayed TaskInfoS2C
        new = self.achievements if all_tasks else self.new_achievements
        if new:
            out.append((4, 2, b"".join(wire.varint(i) for i in new)))
        self.new_achievements = []
        return wire.build(out)


def _matches(code, cond, params) -> bool:
    cond = cond if isinstance(cond, list) else [] if cond is None else [cond]
    if code == FINISH_MATCH:  # [fight type, stage (0 = any)]
        return len(params) >= 2 and len(cond) >= 1 and cond[0] == params[0] and (
            len(cond) < 2 or cond[1] in (0, params[1]))
    if code == CONSUME_CURRENCY:  # currency id
        return not cond or (params and cond[0] == params[0])
    if code == SPEND_CURRENCY:  # [type, currency id]
        return bool(params) and params[0] in cond
    return True
