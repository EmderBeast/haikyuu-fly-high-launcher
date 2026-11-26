"""Offline test of the quiz (0x1A01 tickets, 0x1A02 questions, 0x1A03 reward) as the client's code and the
tables examconf / examquestion / examreward describe it. Temporary accounts, no port.
  python test_exam.py
"""
import asyncio
import random
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-exam-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402
import matches  # noqa: E402
import exam  # noqa: E402
import wire  # noqa: E402

P = matches.P
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


async def call(c, handler, msg_id, kind, pb=b""):
    start = len(c.sent)
    await handler(c, pb)
    sent = c.sent[start:]
    rep = kind()
    rep.ParseFromString(next(p for m, p in reversed(sent) if m == msg_id))
    return rep, [m for m, _ in sent]


async def tickets(c):
    return (await call(c, exam.handle_tickets, exam.MSG_TICKETS, P.ExamTicketsS2C))[0].freetimes


async def questions(c):
    return await call(c, exam.handle_questions, exam.MSG_QUESTIONS, P.ExamQuestionsS2C)


async def answer(c, q, right, wrong=0):
    """Answers the first `right` questions correctly and the next `wrong` ones wrongly, as the client does."""
    req = P.ExamRewardC2S()
    for n, (i, slot) in enumerate(zip(q.questions.id, q.questions.answer_option)):
        if n >= right + wrong:
            break
        req.questions.id.append(i)
        req.questions.answer_option.append(slot if n < right else (slot + 1) % 4)
    return await call(c, exam.handle_reward, exam.MSG_REWARD, P.ExamRewardS2C, req.SerializeToString())


async def main():
    check("tables: 5 free tries, 10 questions, 6 to pass, a paid try costs 10 stamina", (exam.FREE_TRIES, exam.ASKED, exam.NEEDED, exam.cost()) == (5, 10, 6, (105, 10)))
    check("every level has enough questions and the reward 108:10, 131:40, 201:2", all(len(exam.pool(lv)) >= exam.ASKED and exam.reward(lv) == [(108, 10), (131, 40), (201, 2)] for lv in (1, 10, 30, 60)))
    check("level 1 is only asked questions open at level 1", all(exam.QUESTIONS[i]["Level"][0] <= 1 for i in exam.pool(1)) and len(exam.pool(1)) < len(exam.pool(60)))

    c = FakeClient(990741)
    await server.handle_login(c, wire.build([(1, 0, 990741), (7, 2, b"token")]))
    acc = c.account
    check("handlers registered", all(server.MESSAGE_HANDLERS.get(m) for m in (0x1A01, 0x1A02, 0x1A03)))
    check("a new day starts with the free tries", await tickets(c) == 5)

    q, _ = await questions(c)
    ids, slots = list(q.questions.id), list(q.questions.answer_option)
    check("first try: 10 different questions of the player's level, slots 0..3", q.err == 0 and len(set(ids)) == 10 and len(slots) == 10 and set(ids) <= set(exam.pool(acc.level)) and all(0 <= s <= 3 for s in slots))
    check("the try is used up by asking", q.freetimes == 4 and await tickets(c) == 4)

    before = {i: acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i) for i in (108, 131, 201)}
    r, _ = await answer(c, q, 5, 5)
    check("5 right of 10: refused, nothing credited", r.err == exam.FAILED and r.freetimes == 4 and (acc.get_currency(108) if acc.is_currency(108) else acc.item_count(108)) == before[108])
    req = P.ExamRewardC2S()
    req.questions.id.extend([ids[0]] * 6)
    req.questions.answer_option.extend([slots[0]] * 6)
    r, _ = await call(c, exam.handle_reward, exam.MSG_REWARD, P.ExamRewardS2C, req.SerializeToString())
    check("the same question six times: refused", r.err == exam.FAILED)
    other = next(i for i in exam.pool(acc.level) if i not in ids)
    req = P.ExamRewardC2S()
    req.questions.id.extend(ids[:5] + [other])
    req.questions.answer_option.extend(slots[:5] + [0])
    r, _ = await call(c, exam.handle_reward, exam.MSG_REWARD, P.ExamRewardS2C, req.SerializeToString())
    check("a question that was not asked: refused", r.err == exam.FAILED)

    r, order = await answer(c, q, 6, 2)
    after = {i: acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i) for i in (108, 131, 201)}
    check("6 right (8 answered): passed, counter -1", r.err == 0 and r.freetimes == exam.PASSED and await tickets(c) == -1)
    check("reward credited: 108 x10, 131 x40, 201 x2", [after[i] - before[i] for i in (108, 131, 201)] == [10, 40, 2], str([after[i] - before[i] for i in (108, 131, 201)]))
    check("reward window before the reply", server.MSG_NOTIFY_REWARDS in order and order[-1] == exam.MSG_REWARD)
    r, _ = await answer(c, q, 6)
    check("reward a second time: refused", r.err == exam.FAILED and r.freetimes == -1)
    q2, _ = await questions(c)
    check("passed: no more questions today", q2.err == exam.FAILED and not q2.questions.id)

    # failed tries: the free ones run out, then every try costs 10 stamina
    d = FakeClient(990742)
    await server.handle_login(d, wire.build([(1, 0, 990742), (7, 2, b"token")]))
    left = []
    for _ in range(5):
        q, _ = await questions(d)
        left.append(q.freetimes)
    check("five free tries count down to 0", left == [4, 3, 2, 1, 0] and await tickets(d) == 0)
    d.account.set_currency(105, 25)
    d.account.stamina_at = __import__("time").time()
    q, order = await questions(d)
    check("sixth try costs 10 stamina, sent as currency change", q.err == 0 and len(q.questions.id) == 10 and d.account.get_currency(105) == 15 and 0x0405 in order, str(d.account.get_currency(105)))
    d.account.set_currency(105, 9)
    q, _ = await questions(d)
    check("not enough stamina: refused, nothing taken", q.err == exam.FAILED and d.account.get_currency(105) == 9)
    d.account.set_currency(105, 10)
    q, _ = await questions(d)
    r, _ = await answer(d, q, 10)
    check("a paid try can be passed", r.err == 0 and await tickets(d) == -1)
    q_old = q
    check("another account is not affected", pvp.state()["exam"]["990741"]["passed"] and await tickets(c) == -1)

    # a new day: counter and questions start again
    pvp.state()["exam"]["990742"]["day"] = "2000-01-01"
    check("next day: free tries again", await tickets(d) == 5)
    r, _ = await answer(d, q_old, 10)
    check("yesterday's questions do not count", r.err == exam.FAILED)

    # the slot of the correct answer is drawn anew for every question
    random.seed(7)
    seen = set()
    e = FakeClient(990743)
    await server.handle_login(e, wire.build([(1, 0, 990743), (7, 2, b"token")]))
    for _ in range(5):
        q, _ = await questions(e)
        seen |= set(q.questions.answer_option)
    check("all four slots occur", seen == {0, 1, 2, 3})

    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
