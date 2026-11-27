"""Offline test of 0x0408 NotifyStatisticsS2C at login: the characters an account has had, with their
best star and level. The client's bonds hang on it: a new account got an empty list, and its team
screen threw NullReferenceException (PlayerAllFetterData.GetFetterSkillId) as soon as two bond partners
stood in the line-up. Capture: one record per character the account has a card of.
Temporary accounts, no port.
  python test_statistics.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-stat-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import gameconfig  # noqa: E402
import server  # noqa: E402
import matches  # noqa: E402
import wire  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, gid):
        self.gid, self.sent, self.logged_in = gid, [], False

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))

    async def send_raw(self, msg_id, flags, body):
        self.sent.append((msg_id, None))

    def last(self, msg_id):
        return next(p for m, p in reversed(self.sent) if m == msg_id)


def cards_of(acc) -> dict:
    """conf id -> (best star, best level) of the cards the account holds."""
    out = {}
    held = {**acc.b.cards, **{P.PlayerCard.FromString(c).inst_id: c for c in acc.cards}}
    for inst, raw in held.items():
        if inst in acc.removed_cards:
            continue
        c = P.PlayerCard.FromString(raw)
        old = out.get(c.conf_id, (0, 0))
        out[c.conf_id] = (max(old[0], c.star), max(old[1], c.level))
    return out


async def main():
    c = FakeClient(9101)  # a new player
    await server.handle_login(c, wire.build([(1, 0, 9101), (7, 2, b"token")]))
    body = c.last(0x0408)
    check("a new account gets its 0x0408 from the server, not the empty one", bool(body))
    msg = P.NotifyStatisticsS2C.FromString(body)
    rec = {r.player_id: (r.star, r.level) for r in msg.player_record}
    have = cards_of(c.account)
    check("all = true", msg.all)
    check("one record per character he has a card of", bool(have) and set(rec) == set(have), f"{len(rec)} of {len(have)}")
    check("star and level of his cards", all(rec[k] == have[k] for k in have))

    # a card he gets later is in the list of the next login
    conf = next(i for i in sorted(gameconfig.PLAYERS) if i not in have and 1000 < i < 5000)
    c.account.add_card(conf)
    c.account.save()
    again = FakeClient(9101)
    await server.handle_login(again, wire.build([(1, 0, 9101), (7, 2, b"token")]))
    rec2 = {r.player_id for r in P.NotifyStatisticsS2C.FromString(again.last(0x0408)).player_record}
    check("a new card is in the list at the next login", conf in rec2, str(conf))

    # the captured account: every captured record stays, never lower than captured
    owner = accounts.Account(9102, accounts.CAPTURED)
    cap = {r.player_id: (r.star, r.level)
           for r in P.NotifyStatisticsS2C.FromString(accounts.CAPTURED.pb("0x0408")).player_record}
    mine = {r.player_id: (r.star, r.level) for r in P.NotifyStatisticsS2C.FromString(owner.statistics()).player_record}
    check("captured account: the captured records stay", set(cap) <= set(mine), f"{len(cap)} captured, {len(mine)} sent")
    check("... and are never lower", all(mine[k][0] >= cap[k][0] and mine[k][1] >= cap[k][1] for k in cap))

    print(f"\n{sum(checks)}/{len(checks)} ok")
    raise SystemExit(0 if all(checks) else 1)


if __name__ == "__main__":
    asyncio.run(main())
