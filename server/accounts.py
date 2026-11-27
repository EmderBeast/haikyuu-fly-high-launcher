"""
Per-account state on top of a login baseline.

A Baseline is a full set of post-login sync messages: the captured real account
(for owner gids, see owners.json) or a generated starter set (starter.py). It is
sent unchanged at login; each account's own changes (cards, items, currencies,
counters ...) are stored in accounts/<gid>.json and sent after it as the same
update messages the real server used during play:
  0x0902 PlayerCardChangeS2C: repeated PlayerCardInfo (field 1) -> card (field 1)
  0x0802 PackItemChangeS2C:   repeated Item (field 1)

Wire layouts observed in the capture:
  card: 1=inst_id 2=create_time 3=conf_id 4=level 5=exp 6=star ...
  item: 1=inst_id 2=item_id 3=count 4=time
"""
import json
import random
import time
from pathlib import Path

import gameconfig
import wire

DATA = Path(__file__).parent / "data"
ACCOUNTS = Path(__file__).parent / "accounts"

MSG_CARD_CHANGE = 0x0902
MSG_RECALLING_UPDATE = 0x3C02  # RecallingUpdateS2C{1 creates, 2 updates, 3 removes}
MSG_ITEM_CHANGE = 0x0802
MSG_FLAGS = 0x0403  # NotifyFlagsS2C{1=flags}: GAMER_FLAGS bit mask
MSG_CURRENCY_CHANGE = 0x0405  # NotifyCurrencyChangeS2C: repeated ItemCurrency{1=conf_id, 2=number}
MSG_LOTTERY_UPDATE = 0x0F05  # LotteryUpdateS2C: repeated LotteryUnit (field 1)
# LotteryUnit: 1=id 2=all_count 3=today_count 7=ten_num
MSG_TASK_CHANGE = 0x0E02  # TaskChangeS2C{1=repeated Task{1=id 2=state 3=progress 4=finish_num ...}}
TASK_FINISH, TASK_OVER = 1, 2  # completed / reward claimed
MSG_SIGN_INFO = 0x0D01  # SignInfoS2C{1=SignInfo{1=sign_time 2=sign_num 3=month_to_sign 4=sign_total(packed)}}

MSG_TOKYO_INFO = 0x1101  # FightTokyoInfoS2C: all passed Training Camp stages and claimed chapter chests
MSG_TOKYO_UPDATE = 0x1102  # FightTokyoUpdateS2C{1=stage_pass}: Training Camp stage cleared
MSG_STAGE_STARS = 0x2A01  # StageStarInfoS2C{1=all 2=repeated StageStarData{1=stage 2=star mask}}; all=0 merges
MSG_SOURCE_INFO = 0x1501  # FightSourceInfoS2C{1=repeated StageGroupProgress{1=group_id 2=progress}}
MSG_SOURCE_UPDATE = 0x1502
MSG_CONTEST_INFO = 0x1701  # FightConTestInfoS2C{1=ContestData{1=id (highest cleared Extreme Training floor) ...}}
MSG_CONTEST_UPDATE = 0x1702  # FightConTestUpdateS2C{1=ContestData}
MSG_SPECIALTY_INFO = 0x1801  # PlayerSpecialtyInfoS2C{1=repeated PlayerSpecialty}
MSG_SPECIALTY_UPDATE = 0x1802  # PlayerSpecialtyUpdateS2C{1=repeated PlayerSpecialty 3=opt CREATE/UPDATE/REMOVE}
FIGHT_TOKYO = 1  # Training Camp
FIGHT_SOURCE = 3  # Practice Match

STAMINA = 105  # onigiri
# item-table entries that are counters, not inventory: activity points, Match EXP (113, credited to the
# line-up cards by add_card_exp), manager EXP
PSEUDO_ITEMS = {107, 114, 113, 106, 137}
MAX_PLAYER_LEVEL = gameconfig.MAX_PLAYER_LEVEL  # the captured account is max level


def stamina_rules(level: int) -> tuple[int, int]:
    """(seconds per point, regeneration cap) from playerphysical; fallback if the config dump is missing."""
    return gameconfig.stamina_regen(level) or (300, 180)


def _packed(data: bytes) -> list[int]:
    vals, pos = [], 0
    while pos < len(data):
        v, pos = wire.read_varint(data, pos)
        vals.append(v)
    return vals


class Baseline:
    """Everything the account logic needs, derived from one set of login sync messages."""

    def __init__(self, sync: list[dict]):
        self.sync = sync
        self.items = {}
        for _, _, raw in wire.parse(self.pb("0x0801")):
            f = wire.parse(raw)
            self.items[wire.get(f, 2)] = {"inst_id": wire.get(f, 1), "count": wire.get(f, 3, 0)}
        self.cards = {}  # inst_id -> PlayerCard bytes
        for _, _, info in wire.parse(self.pb("0x0901")):
            card = wire.get(wire.parse(info), 1)
            self.cards[wire.get(wire.parse(card), 1)] = card
        self.recallings = {}  # inst_id -> PlayerRecalling bytes (memories, RecallingInfoS2C field 1)
        for num, _, raw in wire.parse(self.pb("0x3C01")):
            if num == 1:
                self.recallings[wire.get(wire.parse(raw), 1)] = raw
        self.max_inst = max([0] + list(self.cards) + [i["inst_id"] for i in self.items.values()]
                            + list(self.recallings))
        self.currency = {}
        for _, _, raw in wire.parse(self.pb("0x0407")):  # NotifyCurrencyInfoS2C
            f = wire.parse(raw)
            self.currency[wire.get(f, 1)] = wire.get(f, 2, 0)
        self.lotteries, self.claimed = {}, {}
        for num, _, raw in wire.parse(self.pb("0x0F01")):  # LotteryInfoS2C: 1 = units, 3 = total rewards
            f = wire.parse(raw)
            if num == 1:
                self.lotteries[wire.get(f, 1)] = raw
            elif num == 3:  # LotteryTotalReward: 1 = lottery id, 3 = claimed milestones (packed)
                self.claimed[wire.get(f, 1)] = _packed(wire.get(f, 3, b""))
        info = wire.parse(self.pb("0x0E01"))  # TaskInfoS2C: 1 tasks, 2 activation, 3 chest states
        self.tasks = {wire.get(wire.parse(raw), 1): raw for num, _, raw in info if num == 1}
        self.activation_fields = wire.parse(wire.get(info, 2, b""))
        self.activation = {"total": wire.get(self.activation_fields, 1, 0),
                           "daily": wire.get(self.activation_fields, 2, 0),
                           "week": wire.get(self.activation_fields, 3, 0)}
        self.chests = {}
        for num, _, raw in info:
            if num == 3:
                f = wire.parse(raw)
                self.chests[wire.get(f, 1)] = wire.get(f, 2, 0)
        # potentials: 1=inst_id 2=conf_id 3=level 5=locked 6=card inst (equipped) 7=main attr 8=vice attrs 9=time
        self.specialties = {wire.get(wire.parse(raw), 1): raw
                            for num, _, raw in wire.parse(self.pb("0x1801")) if num == 1}
        sign = wire.parse(wire.get(wire.parse(self.pb("0x0D01")), 1, b""))
        self.sign = {"time": wire.get(sign, 1, 0), "num": wire.get(sign, 2, 0),
                     "mask": wire.get(sign, 3, 0), "total": _packed(wire.get(sign, 4, b""))}

    def pb(self, msg_id: str) -> bytes:
        for m in self.sync:
            if m["msg_id"] == msg_id:
                return wire.body_to_pb(bytes.fromhex(m["body_hex"]), m["flags"])
        return b""


CAPTURED = Baseline(json.loads((DATA / "login_sync.json").read_text(encoding="utf-8")))
CARD_TEMPLATES = {}
for _card in CAPTURED.cards.values():
    CARD_TEMPLATES.setdefault(wire.get(wire.parse(_card), 3), _card)


def card_info(card: bytes) -> bytes:
    return wire.build([(1, 2, card)])


def item_entry(item_id: int, inst_id: int, count: int) -> bytes:
    return wire.build([(1, 0, inst_id), (2, 0, item_id), (3, 0, count), (4, 0, int(time.time()))])


class Account:
    def __init__(self, gid: int, base: Baseline = CAPTURED):
        self.gid = gid
        self.b = base
        # separate files so a captured-account save never mixes with a starter one
        self.path = ACCOUNTS / (f"{gid}.json" if base is CAPTURED else f"{gid}-starter.json")
        if self.path.exists():
            d = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            d = {"next_inst": base.max_inst + 1, "cards": [], "items": {}}
        self.next_inst = d["next_inst"]
        # cards of characters the running edition's tables do not know (JP-only cards while the server
        # runs "global", see edition.py) are parked: kept in the file, invisible until the tables know them
        held = [bytes.fromhex(c) for c in d["cards"]] + [bytes.fromhex(c) for c in d.get("parked", [])]
        known = lambda c: not gameconfig.AVAILABLE or wire.get(wire.parse(c), 3) in gameconfig.PLAYERS  # noqa: E731
        self.cards = [c for c in held if known(c)]
        self.parked = [c for c in held if not known(c)]
        self.items = {int(k): v for k, v in d["items"].items()}
        self.currency = {int(k): v for k, v in d.get("currency", {}).items()}
        self.lotteries = {int(k): bytes.fromhex(v) for k, v in d.get("lotteries", {}).items()}
        self.pity = {int(k): v for k, v in d.get("pity", {}).items()}
        self.claimed = {int(k): v for k, v in d.get("claimed", {}).items()}
        self.sign = d.get("sign")  # None = unchanged baseline state
        self.tasks = {int(k): bytes.fromhex(v) for k, v in d.get("tasks", {}).items()}
        self.missions = d.get("missions")  # period/activation/chest state, see missions.py
        self.removed_cards = set(d.get("removed_cards", []))  # consumed as upgrade material
        self.stamina_at = d.get("stamina_at", time.time())
        # manager (player) level: the captured account is max level, a starter account begins at 1
        self.level = d.get("level", MAX_PLAYER_LEVEL if base is CAPTURED else 1)
        self.exp = d.get("exp", 0)
        self.stages = {int(k): v for k, v in d.get("stages", {}).items()}  # stage -> [fight type, star mask]
        self.contest = d.get("contest")  # highest cleared Extreme Training floor (None = baseline)
        self.hangup = d.get("hangup")  # Extreme Training idle reward state (hangup.py), None = not started
        self.tokyo_rewards = d.get("tokyo_rewards", [])  # [chapter, star threshold] chests claimed
        # potentials added or changed on top of the baseline (inst_id -> PlayerSpecialty bytes)
        self.specialties = {int(k): bytes.fromhex(v) for k, v in d.get("specialties", {}).items()}
        self.removed_specialties = set(d.get("removed_specialties", []))  # consumed or dismantled (specialty.py)
        self.specialty_settings = d.get("specialty_settings", {})  # auto-dismantle / filter schemes (specialty.py)
        self.admin_flags = d.get("admin_flags", {})  # Admin Mode switches, e.g. infinite_stamina
        # memories (PlayerRecalling) added or changed on top of the baseline: inst_id -> bytes
        self.recallings = {int(k): bytes.fromhex(v) for k, v in d.get("recallings", {}).items()}
        self.removed_recallings = set(d.get("removed_recallings", []))  # consumed or dismantled (recalling.py)
        self.cheers = d.get("cheers") or {}  # cheer -> star_id changed by star ups (cheers.py)
        self.cheer_lottery = d.get("cheer_lottery") or {}  # cheer draw units changed by wish / gifts (cheers.py)
        self.gallery = d.get("gallery") or {}  # gallery bonds / CGs seen, collection rewards (gallery.py)
        self.card_auto = d.get("card_auto")  # auto-dismantle player conf ids (cards.py), None = baseline
        self.recalling_auto = d.get("recalling_auto")  # auto-dismantle conf ids (recalling.py), None = baseline
        self.team = bytes.fromhex(d["team"]) if d.get("team") else None  # TeamInfoS2C with saved line-ups
        self.flags = d.get("flags")  # GAMER_FLAGS bit mask (auto skill 1, auto serve 2, speed 4); None = baseline
        # assistant settings changed since the baseline (ShareAssistSet 0x3703): func_type -> [is_open, arg]
        self.assistant = {int(k): v for k, v in (d.get("assistant") or {}).items()}
        self._regen_stamina()
        import admin  # Admin Mode: apply changes queued by the launcher (no-op without admin.json)
        admin.apply_pending(self)

    def save(self):
        ACCOUNTS.mkdir(exist_ok=True)
        self.path.write_text(json.dumps({
            "next_inst": self.next_inst,
            "cards": [c.hex() for c in self.cards],
            "parked": [c.hex() for c in self.parked],
            "items": {str(k): v for k, v in self.items.items()},
            "currency": {str(k): v for k, v in self.currency.items()},
            "lotteries": {str(k): v.hex() for k, v in self.lotteries.items()},
            "pity": {str(k): v for k, v in self.pity.items()},
            "claimed": {str(k): v for k, v in self.claimed.items()},
            "sign": self.sign,
            "tasks": {str(k): v.hex() for k, v in self.tasks.items()},
            "missions": self.missions,
            "removed_cards": sorted(self.removed_cards),
            "stamina_at": self.stamina_at,
            "level": self.level,
            "exp": self.exp,
            "stages": {str(k): v for k, v in self.stages.items()},
            "contest": self.contest,
            "hangup": self.hangup,
            "tokyo_rewards": self.tokyo_rewards,
            "specialties": {str(k): v.hex() for k, v in self.specialties.items()},
            "removed_specialties": sorted(self.removed_specialties),
            "specialty_settings": self.specialty_settings,
            "admin_flags": self.admin_flags,
            "recallings": {str(k): v.hex() for k, v in self.recallings.items()},
            "removed_recallings": sorted(self.removed_recallings),
            "recalling_auto": self.recalling_auto,
            "card_auto": self.card_auto,
            "gallery": self.gallery,
            "cheers": self.cheers,
            "cheer_lottery": self.cheer_lottery,
            "team": self.team.hex() if self.team else None,
            "flags": self.flags,
            "assistant": {str(k): v for k, v in self.assistant.items()},
        }, indent=1), encoding="utf-8")

    def add_player_exp(self, amount: int) -> list[int]:
        """Add manager EXP (playerlevel LevelExp = EXP from level ID-1 to ID); returns levels reached."""
        reached = []
        self.exp += amount
        while self.level < MAX_PLAYER_LEVEL:
            need = gameconfig.player_level_exp(self.level + 1)
            if not need or self.exp < need:
                break
            self.exp -= need
            self.level += 1
            reached.append(self.level)
        if self.level >= MAX_PLAYER_LEVEL:
            self.exp = 0
        return reached

    def is_currency(self, cid: int) -> bool:
        return cid in self.b.currency or cid in self.currency or cid in CAPTURED.currency

    def get_currency(self, cid: int) -> int:
        return self.currency.get(cid, self.b.currency.get(cid, 0))

    def set_currency(self, cid: int, number: int) -> bytes:
        self.currency[cid] = number
        return wire.build([(1, 0, cid), (2, 0, number)])

    def _regen_stamina(self):
        now = time.time()
        seconds, cap = stamina_rules(self.level)
        ticks = int((now - self.stamina_at) // seconds)
        have = self.get_currency(STAMINA)
        if have >= cap:
            self.stamina_at = now
        elif ticks > 0:
            self.set_currency(STAMINA, min(cap, have + ticks))
            self.stamina_at += ticks * seconds

    def spend_stamina(self, amount: int) -> bytes:
        self._regen_stamina()
        import admin
        if admin.flag(self, "infinite_stamina"):  # Admin Mode switch, off on a multiplayer server
            return self.set_currency(STAMINA, max(self.get_currency(STAMINA), stamina_rules(self.level)[1]))
        if self.get_currency(STAMINA) >= stamina_rules(self.level)[1]:
            self.stamina_at = time.time()
        return self.set_currency(STAMINA, max(0, self.get_currency(STAMINA) - amount))

    def count_pulls(self, lottery_id: int, count: int) -> bytes:
        """Raise a banner's pull counters; returns the updated LotteryUnit."""
        unit = self.lotteries.get(lottery_id) or self.b.lotteries.get(lottery_id) or wire.build([(1, 0, lottery_id)])
        f = wire.parse(unit)
        f = wire.set_varint(f, 2, wire.get(f, 2, 0) + count)
        f = wire.set_varint(f, 3, wire.get(f, 3, 0) + count)
        if count >= 10:
            f = wire.set_varint(f, 7, wire.get(f, 7, 0) + 1)
        self.lotteries[lottery_id] = wire.build(f)
        return self.lotteries[lottery_id]

    def set_wish_list(self, lottery_id: int, conf_ids: list[int]) -> bytes:
        """LotteryUnit.wish_list (12, packed) of a banner; returns the updated unit."""
        unit = self.lotteries.get(lottery_id) or self.b.lotteries.get(lottery_id) or wire.build([(1, 0, lottery_id)])
        f = [x for x in wire.parse(unit) if x[0] != 12]
        if conf_ids:
            f.append((12, 2, b"".join(wire.varint(c) for c in conf_ids)))
        self.lotteries[lottery_id] = wire.build(sorted(f, key=lambda x: x[0]))
        return self.lotteries[lottery_id]

    def draw_cards(self, lottery_id: int, count: int) -> list[int]:
        """Card conf_ids for `count` pulls: real banner rates/pools when the config is available."""
        if not (gameconfig.AVAILABLE and lottery_id in gameconfig.LOTTERIES):
            return random_card_ids(count)
        out = []
        for _ in range(count):
            card, hit = gameconfig.roll(lottery_id, self.pity.get(lottery_id, 0))
            self.pity[lottery_id] = 0 if hit else self.pity.get(lottery_id, 0) + 1
            out.append(card if card else random_card_ids(1)[0])
        return out

    def claimed_milestones(self, lottery_id: int) -> list[int]:
        return self.claimed.get(lottery_id, self.b.claimed.get(lottery_id, []))

    def all_count(self, lottery_id: int) -> int:
        unit = self.lotteries.get(lottery_id) or self.b.lotteries.get(lottery_id) or b""
        return wire.get(wire.parse(unit), 2, 0)

    def claim_milestone(self, lottery_id: int, count: int) -> bytes:
        """Mark a milestone claimed; returns the LotteryTotalReward entry."""
        done = self.claimed_milestones(lottery_id) + [count]
        self.claimed[lottery_id] = done
        return wire.build([(1, 0, lottery_id), (3, 2, b"".join(wire.varint(c) for c in done))])

    def task(self, task_id: int) -> bytes | None:
        return self.tasks.get(task_id) or self.b.tasks.get(task_id)

    def task_state(self, task_id: int) -> int:
        t = self.task(task_id)
        return wire.get(wire.parse(t), 2, 0) if t else 0

    def all_task_ids(self) -> set[int]:
        return set(self.b.tasks) | set(self.tasks)

    def finished_tasks(self) -> list[int]:
        return [i for i in self.all_task_ids() if self.task_state(i) == TASK_FINISH]

    def mark_task_claimed(self, task_id: int) -> bytes:
        f = wire.set_varint(wire.parse(self.task(task_id)), 2, TASK_OVER)
        self.tasks[task_id] = wire.build(f)
        return self.tasks[task_id]

    def sign_state(self) -> dict:
        s = dict(self.sign or self.b.sign)
        last = time.localtime(max(s["time"], 2 * 86400))  # Windows rejects negative timestamps
        now = time.localtime()
        if (last.tm_year, last.tm_mon) != (now.tm_year, now.tm_mon):
            s["mask"] = 0  # new month: calendar starts empty
        return s

    def signed_today(self) -> bool:
        return bool(self.sign_state()["mask"] >> (time.localtime().tm_mday - 1) & 1)

    def sign_in(self) -> dict:
        s = self.sign_state()
        s["mask"] |= 1 << (time.localtime().tm_mday - 1)
        s["num"] += 1
        s["time"] = int(time.time())
        self.sign = s
        return s

    def claim_sign_total(self, sign_id: int):
        s = self.sign_state()
        s["total"] = s["total"] + [sign_id]
        self.sign = s

    def sign_info(self) -> bytes:
        s = self.sign_state()
        return wire.build([(1, 0, s["time"]), (2, 0, s["num"]), (3, 0, s["mask"]),
                           (4, 2, b"".join(wire.varint(x) for x in s["total"]))])

    def add_reward(self, conf_id: int, number: int) -> tuple[int, bytes] | None:
        """Credit a known currency or inventory item; returns (message id, entry), or None if unknown."""
        if self.is_currency(conf_id):
            return MSG_CURRENCY_CHANGE, self.set_currency(conf_id, self.get_currency(conf_id) + number)
        known = conf_id in self.b.items or conf_id in self.items or conf_id in CAPTURED.items
        if known or (gameconfig.AVAILABLE and conf_id in gameconfig.ITEMS and conf_id not in PSEUDO_ITEMS):
            return MSG_ITEM_CHANGE, self.set_item(conf_id, self.item_count(conf_id) + number)
        return None

    def _new_inst(self) -> int:
        self.next_inst += 1
        return self.next_inst - 1

    def item_count(self, item_id: int) -> int:
        if item_id in self.items:
            return self.items[item_id]["count"]
        return self.b.items.get(item_id, {}).get("count", 0)

    def item_by_inst(self, inst_id: int) -> int | None:
        for item_id, v in {**self.b.items, **self.items}.items():
            if v["inst_id"] == inst_id:
                return item_id
        return None

    def set_item(self, item_id: int, count: int) -> bytes:
        inst = self.items.get(item_id, self.b.items.get(item_id, {})).get("inst_id") or self._new_inst()
        self.items[item_id] = {"inst_id": inst, "count": count}
        return item_entry(item_id, inst, count)

    def add_card(self, conf_id: int) -> tuple[int, bytes]:
        inst = self._new_inst()
        card = make_card(inst, conf_id)
        self.cards.append(card)
        return inst, card

    def remove_card(self, inst_id: int) -> bytes | None:
        """Consume a card; returns its last state (for the REMOVE message)."""
        card = self.get_card(inst_id)
        if card is not None:
            self.cards = [c for c in self.cards if wire.get(wire.parse(c), 1) != inst_id]
            self.removed_cards.add(inst_id)
        return card

    def all_recallings(self) -> dict[int, bytes]:
        """inst_id -> PlayerRecalling bytes of every memory the account has."""
        return {k: v for k, v in {**self.b.recallings, **self.recallings}.items() if k not in self.removed_recallings}

    def put_recalling(self, raw: bytes):
        self.recallings[wire.get(wire.parse(raw), 1)] = raw
        self.removed_recallings.discard(wire.get(wire.parse(raw), 1))

    def remove_recalling(self, inst_id: int):
        self.recallings.pop(inst_id, None)
        self.removed_recallings.add(inst_id)

    def add_recalling(self, conf_id: int, level: int = 1, max_level: int = 20, stage: int = 1) -> bytes:
        inst = self._new_inst()
        raw = wire.build([(1, 0, inst), (2, 0, conf_id), (3, 0, level), (4, 0, max_level), (5, 0, stage),
                          (9, 0, int(time.time()))])
        self.recallings[inst] = raw
        return raw

    def get_card(self, inst_id: int) -> bytes | None:
        if inst_id in self.removed_cards:
            return None
        for c in self.cards:
            if wire.get(wire.parse(c), 1) == inst_id:
                return c
        return self.b.cards.get(inst_id)

    def add_card_exp(self, inst_ids: list[int], amount: int) -> list[bytes]:
        """Match EXP (113): every listed card below its level cap gets the full amount on its EXP (field 5)
        and levels up as far as the level table allows; cards at the cap get nothing. Seen on Garena: a
        Practice Match with 113 x300 raised the only line-up card below its cap (lv16, star 2) from 1030
        to 1330 EXP (one 0x0902 UPDATE before the result); the capped line-up cards stayed at 0. The
        result screen shows the bar ("+N" / "MAX") for the 7 starters only, so callers pass the starters.
        Returns the changed cards."""
        changed = []
        for inst in dict.fromkeys(i for i in inst_ids if i):
            card = self.get_card(inst)
            if card is None:
                continue
            f = wire.parse(card)
            conf, level, exp, star = wire.get(f, 3), wire.get(f, 4, 1), wire.get(f, 5, 0), wire.get(f, 6, 0)
            cap = gameconfig.level_cap(star)
            if level >= cap:
                continue
            exp += amount
            while level < cap:
                need = gameconfig.level_up_cost(conf, level, 1)
                if not need or exp < need:
                    break
                exp -= need
                level += 1
            if level >= cap:
                exp = 0
            f = wire.set_varint(f, 4, level)
            f = wire.set_varint(f, 5, exp) if exp else [x for x in f if x[0] != 5]
            card = wire.build(f)
            self.put_card(card)
            changed.append(card)
        return changed

    def put_card(self, card: bytes):
        """Store a new or changed card (replaces the entry with the same inst_id)."""
        inst = wire.get(wire.parse(card), 1)
        self.cards = [c for c in self.cards if wire.get(wire.parse(c), 1) != inst] + [card]

    def get_specialty(self, inst_id: int) -> bytes | None:
        if inst_id in self.removed_specialties:
            return None
        return self.specialties.get(inst_id) or self.b.specialties.get(inst_id)

    def all_specialties(self) -> dict[int, bytes]:
        return {k: v for k, v in {**self.b.specialties, **self.specialties}.items()
                if k not in self.removed_specialties}

    def put_specialty(self, spec: bytes):
        self.specialties[wire.get(wire.parse(spec), 1)] = spec
        self.removed_specialties.discard(wire.get(wire.parse(spec), 1))

    def owned_conf_ids(self) -> set[int]:
        cards = {**self.b.cards, **{wire.get(wire.parse(c), 1): c for c in self.cards}}
        return {wire.get(wire.parse(c), 3) for i, c in cards.items() if i not in self.removed_cards}

    def statistics(self) -> bytes:
        """0x0408 NotifyStatisticsS2C{1=all 2=PlayerRecord{1=player 2=star 3=level}}: the best star and
        level the player ever had of every character. In the capture it holds every character the
        account has a card of (133 of 133, plus 4 he no longer has). The client works out from it which
        bonds are collected; with no record at all (a new account) its bond table is never set up and
        the team screen throws NullReferenceException in PlayerAllFetterData.GetFetterSkillId as soon
        as bond partners stand in a line-up. So: the baseline records raised by the cards held now."""
        best = {}
        raw = self.b.pb("0x0408")
        for num, _, v in wire.parse(raw) if raw else []:
            if num == 2:
                f = wire.parse(v)
                best[wire.get(f, 1, 0)] = (wire.get(f, 2, 0), wire.get(f, 3, 0))
        held = {**self.b.cards, **{wire.get(wire.parse(c), 1): c for c in self.cards}}
        for inst, card in held.items():
            if inst in self.removed_cards:
                continue
            f = wire.parse(card)
            conf, star, level = wire.get(f, 3, 0), wire.get(f, 6, 0), wire.get(f, 4, 0)
            old = best.get(conf, (0, 0))
            best[conf] = (max(old[0], star), max(old[1], level))
        return wire.build([(1, 0, 1)] + [(2, 2, wire.build([(1, 0, c), (2, 0, st), (3, 0, lv)]))
                                         for c, (st, lv) in best.items()])

    def team_info(self) -> bytes:
        """0x0B01 TeamInfoS2C: the baseline line-ups with the ones saved since (0x0B02). Cards a saved
        line-up names but the account no longer has (removed, or gone from the starter set such as the
        5xxxx copies) become empty places, so the client never gets a line-up with an unknown card."""
        if not self.team:
            return self.b.pb("0x0B01")
        out = []
        for num, wt, v in wire.parse(self.team):
            if num != 1:
                out.append((num, wt, v))
                continue
            form = []
            for n, w, x in wire.parse(v):
                if n == 3:
                    ids = _packed(x) if w == 2 else [x]
                    # the place in the list is the place in the line-up: a missing card becomes an
                    # empty place (0), as in the captured line-ups
                    keep = [i if i == 0 or self.get_card(i) is not None else 0 for i in ids]
                    form.append((3, 2, b"".join(wire.varint(i) for i in keep)))
                else:
                    form.append((n, w, x))
            out.append((1, 2, wire.build(form)))
        return wire.build(out)

    def save_formation(self, formation: bytes):
        """Stores a TeamFormation (0x0B02) in place of the line-up with the same number (field 1)."""
        lineup = wire.get(wire.parse(formation), 1, 0)
        out, done = [], False
        for num, wt, v in wire.parse(self.team_info()):
            if num == 1 and wire.get(wire.parse(v), 1, 0) == lineup:
                if not done:
                    out.append((1, 2, formation))
                done = True
            else:
                out.append((num, wt, v))
        if not done:
            out.insert(0, (1, 2, formation))
        self.team = wire.build(out)
        self.save()

    def login_updates(self) -> list[tuple[int, bytes]]:
        """Update messages that re-apply this account's changes after the baseline sync."""
        out = []
        # PlayerCardChangeS2C.opt (2): CREATE 0 for new cards, UPDATE 1 for changed baseline ones
        created = [c for c in self.cards if wire.get(wire.parse(c), 1) not in self.b.cards]
        updated = [c for c in self.cards if wire.get(wire.parse(c), 1) in self.b.cards]
        if created:
            out.append((MSG_CARD_CHANGE, b"".join(wire.build([(1, 2, card_info(c))]) for c in created)))
        if updated:
            out.append((MSG_CARD_CHANGE, b"".join(wire.build([(1, 2, card_info(c))]) for c in updated)
                        + wire.build([(2, 0, 1)])))
        gone = [self.b.cards[i] for i in self.removed_cards if i in self.b.cards]
        if gone:  # opt REMOVE (2)
            out.append((MSG_CARD_CHANGE, b"".join(wire.build([(1, 2, card_info(c))]) for c in gone)
                        + wire.build([(2, 0, 2)])))
        if self.items:
            out.append((MSG_ITEM_CHANGE, b"".join(
                wire.build([(1, 2, item_entry(k, v["inst_id"], v["count"]))]) for k, v in self.items.items())))
        if self.currency:
            out.append((MSG_CURRENCY_CHANGE, b"".join(
                wire.build([(1, 2, wire.build([(1, 0, k), (2, 0, v)]))]) for k, v in self.currency.items())))
        if self.sign:
            out.append((MSG_SIGN_INFO, wire.build([(1, 2, self.sign_info())])))
        if self.lotteries or self.claimed:
            # LotteryUpdateS2C: 1 = units, 4 = total rewards (claimed milestones)
            out.append((MSG_LOTTERY_UPDATE, b"".join(wire.build([(1, 2, u)]) for u in self.lotteries.values())
                        + b"".join(wire.build([(4, 2, wire.build([(1, 0, k), (3, 2, b"".join(
                            wire.varint(c) for c in v))]))]) for k, v in self.claimed.items())))
        # PlayerSpecialtyUpdateS2C opt CREATE 0 / REMOVE 2; changed baseline potentials are replaced
        # (REMOVE + CREATE) because the client merges UPDATEs and would keep a cleared owner
        changed = [v for k, v in self.specialties.items() if k in self.b.specialties]
        if changed:
            out.append((MSG_SPECIALTY_UPDATE, b"".join(wire.build([(1, 2, s)]) for s in changed)
                        + wire.build([(3, 0, 2)])))
        if self.specialties:
            out.append((MSG_SPECIALTY_UPDATE, b"".join(wire.build([(1, 2, s)]) for s in self.specialties.values())))
        gone_recs = sorted(i for i in self.removed_recallings if i in self.b.recallings)
        if self.recallings or gone_recs:  # RecallingUpdateS2C: 1 = creates, 2 = updates, 3 = removes
            new = [r for k, r in self.recallings.items() if k not in self.b.recallings]
            changed = [r for k, r in self.recallings.items() if k in self.b.recallings]
            out.append((MSG_RECALLING_UPDATE, b"".join(wire.build([(1, 2, r)]) for r in new)
                        + b"".join(wire.build([(2, 2, r)]) for r in changed)
                        + (wire.build([(3, 2, b"".join(wire.varint(i) for i in gone_recs))]) if gone_recs else b"")))
        if self.flags is not None:  # NotifyFlagsS2C{1=flags}: auto play / auto serve / speed switches
            out.append((MSG_FLAGS, wire.build([(1, 0, self.flags)])))
        if self.contest is not None:
            out.append((MSG_CONTEST_UPDATE, wire.build([(1, 2, self.contest_data())])))
        if self.stages or self.tokyo_rewards:
            out.append((MSG_TOKYO_INFO, self.tokyo_info()))
            out.append((MSG_SOURCE_INFO, b"".join(wire.build([(1, 2, wire.build([(1, 0, g), (2, 0, s)]))])
                                                  for g, s in self.source_progress().items())))
            out += [u for u in self.stage_updates(self.stages) if u[0] == MSG_STAGE_STARS]
        return out

    def clear_stage(self, fight_type: int, stage_id: int, stars: int) -> tuple[bool, list]:
        """Record a won stage; returns (first clear, update messages)."""
        old = self.stages.get(stage_id)
        first = old is None
        mask = stars | (old[1] if old else 0)
        self.stages[stage_id] = [fight_type, mask]
        updates = [u for u in self.stage_updates({stage_id: self.stages[stage_id]}) if u[0] != MSG_SOURCE_UPDATE]
        group = gameconfig.source_group(stage_id) if fight_type == FIGHT_SOURCE else None
        if group:  # furthest stage of the ticket, never a step back
            best = self.source_progress().get(group[0], stage_id)
            updates.append((MSG_SOURCE_UPDATE, wire.build([(1, 2, wire.build([(1, 0, group[0]), (2, 0, best)]))])))
        return first, updates

    def contest_data(self) -> bytes:
        """ContestData from the baseline login message with this account's cleared floor."""
        info = wire.parse(self.b.pb("0x1701"))
        data = wire.parse(wire.get(info, 1, b""))
        if self.contest is not None:
            data = [(n, t, v) for n, t, v in data if n != 1] + [(1, 0, self.contest)]
        if self.hangup:  # this player's idle reward start, not the captured one
            data = [(n, t, v) for n, t, v in data if n != 5] + [(5, 0, int(self.hangup["start"]))]
        return wire.build([(n, t, v) for n, t, v in sorted(data, key=lambda x: x[0])])

    def contest_floor(self) -> int:
        """Highest cleared Extreme Training floor."""
        return wire.get(wire.parse(self.contest_data()), 1, 0)

    def clear_contest(self, floor: int) -> list[tuple[int, bytes]]:
        """Extreme Training floor won: raise the cleared floor (never lower it); update message."""
        if floor <= self.contest_floor():
            return []
        self.contest = floor
        return [(MSG_CONTEST_UPDATE, wire.build([(1, 2, self.contest_data())]))]

    def stage_stars(self) -> dict[int, int]:
        """stage -> star mask: baseline StageStarInfo merged with this account's clears."""
        out = {}
        for num, _, raw in wire.parse(self.b.pb("0x2A01")):
            if num == 2:
                f = wire.parse(raw)
                out[wire.get(f, 1, 0)] = wire.get(f, 2, 0)
        for sid, (_, mask) in self.stages.items():
            out[sid] = out.get(sid, 0) | mask
        return out

    def tokyo_info(self) -> bytes:
        """FightTokyoInfoS2C{1=stages_pass (packed) 2=rewards_got FightTokyoReward{1=chapter 2=progress}}."""
        base = wire.parse(self.b.pb("0x1101"))
        passed = []
        for num, wt, v in base:
            if num == 1:
                passed += _packed(v) if wt == 2 else [v]
        passed += [sid for sid, (ft, _) in self.stages.items() if ft == FIGHT_TOKYO and sid not in passed]
        got = [v for num, _, v in base if num == 2]
        got += [wire.build([(1, 0, c), (2, 0, p)]) for c, p in self.tokyo_rewards]
        return wire.build([(1, 2, b"".join(wire.varint(s) for s in passed))] + [(2, 2, g) for g in got])

    def source_progress(self) -> dict[int, int]:
        """Practice Match ticket -> furthest cleared stage (baseline FightSourceInfo + this account's clears)."""
        out = {}
        for num, _, raw in wire.parse(self.b.pb("0x1501")):
            if num == 1:
                f = wire.parse(raw)
                out[wire.get(f, 1, 0)] = wire.get(f, 2, 0)
        for sid, (ft, _) in self.stages.items():
            group = gameconfig.source_group(sid) if ft == FIGHT_SOURCE else None
            if group:
                gid, order = group
                cur = out.get(gid)
                if cur not in order or order.index(sid) > order.index(cur):
                    out[gid] = sid
        return out

    def tokyo_claimed(self, chapter: int) -> set[int]:
        out = {p for c, p in self.tokyo_rewards if c == chapter}
        for num, _, v in wire.parse(self.b.pb("0x1101")):
            if num == 2 and wire.get(wire.parse(v), 1) == chapter:
                out.add(wire.get(wire.parse(v), 2, 0))
        return out

    @staticmethod
    def stage_updates(stages: dict) -> list[tuple[int, bytes]]:
        # FightTokyoUpdateS2C{1=stage_pass} per Training Camp stage; StageStarInfoS2C{1=all 2=StageStarData{1 id 2 star}}
        out = [(MSG_TOKYO_UPDATE, wire.build([(1, 0, sid)])) for sid, (ft, _) in stages.items() if ft == FIGHT_TOKYO]
        if stages:
            out.append((MSG_STAGE_STARS, wire.build([(2, 2, wire.build([(1, 0, sid), (2, 0, mask)]))
                                                     for sid, (_, mask) in stages.items()])))
        return out


def make_card(inst: int, conf_id: int) -> bytes:
    """A freshly obtained card (level 1, starting star, its skills from skilltemplate)."""
    fresh = gameconfig.new_card_fields(conf_id)
    if fresh:
        star, skills = fresh
        # PlayerCard: 1=inst_id 2=create_time 3=conf_id 4=level 6=star 8=skills (packed)
        return wire.build([(1, 0, inst), (2, 0, int(time.time())), (3, 0, conf_id), (4, 0, 1),
                           (6, 0, star), (8, 2, b"".join(wire.varint(s) for s in skills))])
    f = wire.parse(CARD_TEMPLATES[conf_id])
    f = [x for x in f if x[0] != 5]  # no exp
    f = wire.replace(f, 1, inst)
    f = wire.replace(f, 2, int(time.time()))
    f = wire.replace(f, 4, 1)  # level 1
    return wire.build(f)


def random_card_ids(n: int) -> list[int]:
    return random.choices(list(CARD_TEMPLATES), k=n)
