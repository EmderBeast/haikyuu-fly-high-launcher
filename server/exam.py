"""
Quiz (client: QuizGame; 0x1A01 ExamTickets, 0x1A02 ExamQuestions, 0x1A03 ExamReward), per player and day.

Before, 0x1A01 was replayed from the capture (always -1 = "done today", the owner's state) and the two
other requests were only confirmed: nobody could play the quiz.

No capture holds a played quiz, so this follows the client's code (decoded/analysis/hotfix_il_full.txt)
and the tables examconf / examquestion / examreward:
  - freetimes (ExamTicketsS2C, also in the two other replies) is the client's counter
    (QuizGameMessageHandler::SetQuizGameRed): -1 = passed today (QuizGameEndLogic shows "Success"), 0 = the
    next try costs (QuizGameViewUtils::IsNeedConsume: count == 0), otherwise free tries left. All six
    captured 0x1A01 replies are -1 (an account that had passed).
  - a paid try costs examconf 2 = "105:10" (10 stamina; QuizGameLoadLogic::OnExamButtonEvent checks
    PhysicalIsEnough before asking)
  - 0x1A02 has no fields; the reply carries questions{id[], answer_option[]}. The correct answer of a
    question is always AnswerA (QuizGameViewUtils::GetCorrectAnswer looks for that text); the client
    shuffles the four answers and then moves the correct one to the slot answer_option names
    (GetRandomResort), so answer_option is the slot 0..3 of the correct answer, chosen by the server
  - examconf 3 = 10 questions per try, examconf 4 = 6 correct answers to pass (QuizGameViewLogic)
  - the client sends 0x1A03 only when the 6th correct answer is in (OnNumChanged): questions{id = the
    questions answered so far, answer_option = the chosen slots}. A failed try sends nothing, so the try
    is used up by 0x1A02.
  - reward: examreward row of the player's level (QuizGameViewUtils::GetQuizRewards), every row
    108:10, 131:40, 201:2. The assistant capture (decrypted_1) shows the same three items for the quiz
    and 0x1A01 = -1 afterwards.

Assumptions:
  - examconf 1 = 5 is the number of free tries per day (the client only prints it in the rule text)
  - 0x1A02 replies with the free tries left after this one; the day changes with the daily missions
  - examquestion.Level = [from, to] player level a question is asked at
  - 0x1A03 is checked against the questions handed out (at least 6 right, each answered once);
    order: rewards with their window, then the reply; refusals are FAILED with the current counter
State: pvp.json "exam".
"""
import logging
import random
import time

import gameconfig
import matches
import missions
import pvp

P = matches.P
log = logging.getLogger("hfh")

MSG_TICKETS, MSG_QUESTIONS, MSG_REWARD = 0x1A01, 0x1A02, 0x1A03
FAILED = 1
PASSED = -1
STAMINA = 105


def _load(name: str) -> dict:
    return gameconfig._table(name) if gameconfig.AVAILABLE and (gameconfig.CFG / f"{name}.json").exists() else {}


CONF = _load("examconf")
QUESTIONS = _load("examquestion")
REWARDS = _load("examreward")


def _value(row: int, default: int) -> int:
    v = CONF.get(row, {}).get("Value")
    v = v[0] if isinstance(v, list) and v else v
    return v if isinstance(v, int) and v > 0 else default


FREE_TRIES = _value(1, 5)
ASKED = _value(3, 10)
NEEDED = _value(4, 6)


def cost() -> tuple[int, int]:
    item, _, number = str(CONF.get(2, {}).get("Data") or "").partition(":")
    return (int(item), int(number)) if item.isdigit() and number.isdigit() else (STAMINA, 10)


def _in_level(row: dict, level: int) -> bool:
    rng = row.get("Level")
    return not (isinstance(rng, list) and len(rng) == 2) or rng[0] <= level <= rng[1]


def pool(level: int) -> list[int]:
    return [i for i, r in QUESTIONS.items() if _in_level(r, level)]


def reward(level: int) -> list[tuple[int, int]]:
    row = next((r for r in REWARDS.values() if _in_level(r, level)), None)
    return gameconfig.parse_rewards(row.get("Reward")) if row else []


def _state(client) -> dict:
    """This account's quiz day; a new day (daily mission reset) starts with all free tries."""
    day = missions.periods(time.time())["daily"]
    store = pvp.state().setdefault("exam", {})
    st = store.get(str(client.gid))
    if not st or st.get("day") != day:
        st = store[str(client.gid)] = {"day": day, "used": 0, "passed": False, "ids": [], "slots": []}
    return st


def freetimes(client) -> int:
    st = _state(client)
    return PASSED if st["passed"] else max(0, FREE_TRIES - st["used"])


async def handle_tickets(client, pb: bytes):
    await client.send_packet(MSG_TICKETS, P.ExamTicketsS2C(freetimes=freetimes(client)).SerializeToString())


async def handle_questions(client, pb: bytes, rng=random):
    st, acc = _state(client), client.account
    ids = pool(acc.level)
    paid = not st["passed"] and st["used"] >= FREE_TRIES
    item, number = cost()
    if paid and item == STAMINA:
        acc._regen_stamina()
    if st["passed"] or len(ids) < ASKED or (paid and acc.get_currency(item) < number):
        log.info(f"  EXAM questions refused (passed {st['passed']}, used {st['used']})")
        await client.send_packet(MSG_QUESTIONS, P.ExamQuestionsS2C(freetimes=freetimes(client), err=FAILED).SerializeToString())
        return
    if paid:
        entry = acc.spend_stamina(number) if item == STAMINA else acc.set_currency(item, acc.get_currency(item) - number)
        acc.save()
        import wire
        await client.send_packet(0x0405, wire.build([(1, 2, entry)]))
    st["used"] += 1
    st["ids"] = rng.sample(ids, ASKED)
    st["slots"] = [rng.randrange(4) for _ in st["ids"]]
    pvp.save()
    out = P.ExamQuestionsS2C(freetimes=freetimes(client))
    out.questions.id.extend(st["ids"])
    out.questions.answer_option.extend(st["slots"])
    log.info(f"  EXAM try {st['used']} ({'paid' if paid else 'free'}), {ASKED} questions")
    await client.send_packet(MSG_QUESTIONS, out.SerializeToString())


def correct(st: dict, ids: list[int], chosen: list[int]) -> int:
    """Right answers of a 0x1A03 request; -1 when it does not fit the questions handed out."""
    if len(ids) != len(chosen) or len(set(ids)) != len(ids) or not set(ids) <= set(st["ids"]):
        return -1
    slot = dict(zip(st["ids"], st["slots"]))
    return sum(1 for i, c in zip(ids, chosen) if slot[i] == c)


async def handle_reward(client, pb: bytes):
    st = _state(client)
    req = P.ExamRewardC2S.FromString(pb)
    right = -1 if st["passed"] else correct(st, list(req.questions.id), list(req.questions.answer_option))
    if right < NEEDED:
        log.info(f"  EXAM reward refused ({right} right, passed {st['passed']})")
        await client.send_packet(MSG_REWARD, P.ExamRewardS2C(err=FAILED, freetimes=freetimes(client)).SerializeToString())
        return
    st.update(passed=True, ids=[], slots=[])
    pvp.save()
    rewards = reward(client.account.level)
    await pvp.HOOKS["grant_rewards"](client, rewards, True)
    log.info(f"  EXAM passed with {right} right: {rewards}")
    await client.send_packet(MSG_REWARD, P.ExamRewardS2C(freetimes=PASSED).SerializeToString())


HANDLERS = {MSG_TICKETS: handle_tickets, MSG_QUESTIONS: handle_questions, MSG_REWARD: handle_reward}
