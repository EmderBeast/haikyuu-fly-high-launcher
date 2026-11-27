"""Offline test of the gallery (gallery.py), captured account in a temporary folder:
  - collection value of the captured account reaches exactly its 164 claimed rows (as Garena's state)
  - 0x1D05 with a new character: the newly reached rows, items credited, ids in the reply; nothing left -> FAILED
  - 0x1D03 / 0x1D04 seen lists, kept in 0x1D01 after a new login
  python test_gallery.py
"""
import asyncio
import tempfile
from pathlib import Path

import accounts

TMP = Path(tempfile.mkdtemp(prefix="hfh-gallery-"))
accounts.ACCOUNTS = TMP
import pvp  # noqa: E402
pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server  # noqa: E402,F401
import admin  # noqa: E402
admin.CONFIG = TMP / "admin.json"
import gallery as G  # noqa: E402
import gameconfig  # noqa: E402
import matches  # noqa: E402

P = matches.P
checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


class FakeClient:
    def __init__(self, acc):
        self.gid, self.sent, self.writer, self.account = acc.gid, [], None, acc

    async def send_packet(self, msg_id, payload, encrypted=True, compress=False):
        self.sent.append((msg_id, payload))


async def main():
    acc = accounts.Account(9831, accounts.CAPTURED)
    c = FakeClient(acc)
    value = G.collect_value(acc)
    reach = {i for i, r in G.REWARDS.items() if (r.get("CollectValue") or 0) <= value}
    claimed = set(G.state(acc).collect_awarded)
    check("captured collection value reaches exactly the claimed rows", reach == claimed and len(claimed) == 164,
          (value, len(reach), len(claimed)))
    await G.handle_collect(c, b"")
    check("0x1D05 with nothing new: FAILED", P.IllustratedCollectRewardsS2C.FromString(c.sent[-1][1]).err == P.FAILED)

    held = acc.owned_conf_ids()
    for conf, row in sorted(gameconfig.PLAYERS.items()):
        if conf not in held and row.get("Rare") in (5, 6) and conf < 10000:
            acc.add_card(conf)
            if G.collect_value(acc) >= min((r.get("CollectValue") or 0) for i, r in G.REWARDS.items() if i not in claimed):
                break
    due = [i for i, r in sorted(G.REWARDS.items()) if i not in claimed and (r.get("CollectValue") or 0) <= G.collect_value(acc)]
    gain = {}
    for i in due:
        for item, n in G.items(G.REWARDS[i]):
            gain[item] = gain.get(item, 0) + n
    before = {i: (acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i)) for i in gain}
    c.sent.clear()
    await G.handle_collect(c, b"")
    rep = P.IllustratedCollectRewardsS2C.FromString(c.sent[-1][1])
    after = {i: (acc.get_currency(i) if acc.is_currency(i) else acc.item_count(i)) - before[i] for i in gain}
    check("0x1D05: newly reached rows in the reply, items credited", due and list(rep.ids) == due and after == gain,
          (due, after, gain))

    import bondlevels
    fetter = next(b["ID"] for b in bondlevels.BONDS if b["ID"] not in G.state(acc).fetter_ids)
    await G.handle_fetter_see(c, P.IllustratedFetterSeeC2S(ferrer_id=[fetter]).SerializeToString())
    await G.handle_cg_see(c, P.IllustratedCgSeeC2S(cg_id=12345).SerializeToString())
    again = accounts.Account(9831, accounts.CAPTURED)
    info = P.IllustratedInfoS2C.FromString(G.info(again, again.b.pb("0x1D01")))
    check("new login: seen bond, seen CG, claimed rows in 0x1D01",
          fetter in info.fetter_ids and 12345 in info.cg_ids and set(due) <= set(info.collect_awarded) and info.all)


if __name__ == "__main__":
    if not G.REWARDS:
        print("skip: tables missing (gamedata)")
    else:
        asyncio.run(main())
        print(f"{sum(checks)}/{len(checks)} checks passed", TMP)
        raise SystemExit(0 if all(checks) else 1)
