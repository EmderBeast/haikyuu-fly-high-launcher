"""Offline test of the power formula (power.py) against the captured rooms (decoded/*match_msgs.json):
  - card power: matches.card_entry on the captured account's cards as they stood in the rooms, with the
    account's records and gallery rows, = Garena's PlayerCardInfo.power (28 of 31 card states exact since
    DefCritRate is read; the other 3 are 90 / 108 below; before the formula: about 3 800 below on average)
  - team power: cards + weighted team shares (cheers from the tables + coach) + battle bonds + coach force
    = Garena's PVPTeamInfo.power (97 of 112 captured home rooms of the account within 4; 15 with -43 /
    +1 077 / +1 188, a rest that is open)
  python test_power.py
"""
import collections
import json
from pathlib import Path

import accounts
import cheers
import matches
import power
import wire

P = matches.P
DECODED = Path(__file__).parent.parent / "decoded"
GID = 1940388811
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def body(m):
    b = bytes.fromhex(m["hex"])
    return b[1:] if b[:1] == b"\x00" else b


def rooms():
    for path in sorted(DECODED.glob("*match_msgs.json")):
        for m in json.loads(path.read_text()):
            if m["dir"] == "S->C" and m["msg_id"] == "0x0302":
                yield P.PVPCreateS2C.FromString(body(m)).info


def main():
    base = accounts.CAPTURED
    records = {}
    for num, _, v in wire.parse(base.pb("0x0408")):
        if num == 2:
            f = wire.parse(v)
            records[wire.get(f, 1, 0)] = wire.get(f, 2, 0)
    awarded = list(P.IllustratedInfoS2C.FromString(base.pb("0x1D01")).collect_awarded)
    acc = accounts.Account(GID, base)
    cards, teams = {}, collections.Counter()
    for info in rooms():
        t = info.home
        if t.gamer_id != GID:
            continue
        for x in t.formation.players:
            if x.info.conf_id:
                cards.setdefault((x.info.inst_id, x.power), x)
        share = cheers.stat_share(acc, list(t.formation.player_supports))
        for k, v in matches.coach_values(t.formation.coach_card if t.formation.HasField("coach_card") else None).items():
            share[k] = share.get(k, 0) + v
        teams[t.power - power.team_power(t, share)] += 1
    if not cards:
        print("skip (no decoded/*match_msgs.json)")
        return
    diffs = collections.Counter()
    for (_, pw), x in cards.items():
        card = P.PlayerCard()
        card.CopyFrom(x.info)
        rec = x.recalling_entity.SerializeToString() if x.HasField("recalling_entity") else None
        e = matches.card_entry(card, recalling=rec, specialties=[s.SerializeToString() for s in x.specialtys_entity],
                               context=(records, awarded))
        diffs[pw - e.power] += 1
    near = sum(n for d, n in diffs.items() if abs(d) <= 150)
    check("card power: exact on at least 28 of 31 card states, all within 150", diffs[0] >= 28 and near == sum(diffs.values()),
          sorted(diffs.items(), key=lambda kv: -kv[1]))
    ok = sum(n for d, n in teams.items() if abs(d) <= 4)
    check("team power: formula within 4 on all but the known -43 / +1 077 / +1 188 rooms",
          ok + teams[-43] + teams[1077] + teams[1188] == sum(teams.values()) and ok >= 90, sorted(teams.items()))


if __name__ == "__main__":
    if not power.PPF:
        print("skip: tables missing (gamedata)")
    else:
        main()
        print(f"{sum(checks)}/{len(checks)} checks passed")
        raise SystemExit(0 if all(checks) else 1)
