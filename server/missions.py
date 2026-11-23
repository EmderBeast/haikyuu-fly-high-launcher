"""
Daily/weekly missions: resets, progress from server-side events, activity points and chests.

Config (taskdepotconfig): TaskType 1 = daily, 6 = weekly. TaskSubTargetType is the
condition code, TastConditionTow its parameters, Value the target.
Rewards 107 / 114 are daily / weekly activity points (TaskActivation fields 2 / 3,
plus 1 = total), not currencies. Activity chests: dailyactivityreward (Type 1 daily, 6 weekly).

Wire: Task{1=id 2=state 3=progress}, TaskActivation{1=total 2=daily 3=week},
TaskActivationRewardState{1=id 2=state}; TaskChangeS2C{1=tasks 2=activation 3=chest states}.
"""
import time

import gameconfig
import wire
from accounts import TASK_FINISH

DAILY, WEEKLY = 1, 6
RESET_HOUR = 6  # "Resets Daily at 6:00 AM"
DAILY_POINTS, WEEKLY_POINTS = 107, 114
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
        self.chests = {int(k): v for k, v in (d.get("chests") or acc.b.chests).items()}
        self.changed_tasks = []

    def save(self):
        self.acc.missions = {"period": self.period, "activation": self.activation,
                             "chests": {str(k): v for k, v in self.chests.items()}}

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
        out.append((2, 2, wire.build(act)))
        out += [(3, 2, wire.build([(1, 0, cid), (2, 0, st)])) for cid, st in sorted(self.chests.items())]
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
