"""Offline test: potentials and memories count in match stats (matches.card_entry / home_team), checked
against the captured home team of 0x0302:
  - val = star / level exactly as Garena sends it, for every captured card;
  - extrval: Garena's minus ours leaves per stat only the team-wide coach / bond share, which is the same on
    most cards (a round amount per stat on at least 4 of 7, e.g. stat 2: 6560000, stat 6: 7020000; single
    cards have a per-card bond extra on some stats), and the crit / bonus stats
    9-14 come out exactly;
  - a card with Admin Mode's perfect potentials (admin.sync_perfect) is stronger in home_team than before,
    a captured card keeps its coach / bond share when its potentials change.
Temporary accounts.
  python test_potential_stats.py
"""
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-potstats-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import matches  # noqa: E402

P = matches.P


def entry_of(p):
    card = P.PlayerCard()
    card.CopyFrom(p.info)
    rec = p.recalling_entity.SerializeToString() if p.HasField("recalling_entity") else None
    return matches.card_entry(card, recalling=rec, specialties=[s.SerializeToString() for s in p.specialtys_entity])


def main():
    home = matches._captured("0x0302", P.PVPCreateS2C).info.home
    rests = {}
    for p in home.formation.players[:7]:
        e = entry_of(p)
        real = {a.type: a for a in p.attr}
        ours = {a.type: a for a in e.attr}
        for t in range(7):
            assert ours[t].val == real[t].val, (p.info.conf_id, t, ours[t].val, real[t].val)
            rests.setdefault(t, []).append(real[t].extrval - ours[t].extrval)
        for t in range(9, 15):  # crit / bonus stats: only potentials, exact
            assert ours[t].extrval == real[t].extrval, (p.info.conf_id, t, ours[t].extrval, real[t].extrval)
        assert len(e.specialtys_entity) == len(p.specialtys_entity)
    print("rest per stat:", {t: sorted(set(v)) for t, v in rests.items()})
    for t, v in rests.items():  # coach / bond share: never below 0 (we never count more than Garena),
        assert min(v) >= 0 and min(v) % 10000 == 0, (t, v)  # its smallest amount per stat is round
        common = max(set(v), key=v.count)
        assert v.count(common) >= 2 and common % 10000 == 0, (t, v)

    # Admin Mode's perfect potentials make a card stronger
    acc = accounts.Account(9995, accounts.CAPTURED)
    lineup = matches._lineup(acc, P.FIGHT_TOKYO)[:7]
    template = matches._captured("0x0302", P.PVPCreateS2C).info.home
    before = matches.home_team(template, acc, 9995, "Emder", P.FIGHT_TOKYO)
    admin.flag = lambda a, k: True
    assert admin.sync_perfect(acc) > 0
    after = matches.home_team(template, acc, 9995, "Emder", P.FIGHT_TOKYO)
    b = {p.info.inst_id: p for p in before.formation.players}
    a = {p.info.inst_id: p for p in after.formation.players}
    stronger = 0
    for inst in lineup:
        if inst not in a:
            continue
        assert len(a[inst].specialtys_entity) == 6
        tot = lambda p: sum(x.val + x.extrval for x in p.attr if x.type < 7)
        stronger += tot(a[inst]) > tot(b[inst])
        if inst in {p.info.inst_id for p in template.formation.players}:  # captured: coach / bonds stay
            real = next(p for p in template.formation.players if p.info.inst_id == inst)
            base = {x.type: x.val for x in real.attr}
            old = matches.potential_stats(base, list(real.specialtys_entity))
            new = matches.potential_stats(base, list(a[inst].specialtys_entity))
            for x, y in zip(sorted(real.attr, key=lambda z: z.type), sorted(a[inst].attr, key=lambda z: z.type)):
                assert y.extrval == x.extrval - old.get(x.type, 0) + new.get(x.type, 0)
    print("stronger starters:", stronger, "of", len(lineup), "power", before.power, "->", after.power)
    assert stronger >= 5 and after.power > before.power
    print("OK", TMP)


main()
