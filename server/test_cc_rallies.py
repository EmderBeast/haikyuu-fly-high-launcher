"""Club boss (CC) rallies like Garena's captured boss match (engine.CC_*): the user's team wins every
rally (11-0 in the capture), the boss digs the user's first spike back and the second one scores, so a
rally has two user spikes and runs about as long as Garena's (~290 frames, 11 rallies in 300 s); other
match types keep their rallies."""
import random, tempfile
from pathlib import Path
import accounts
TMP = Path(tempfile.mkdtemp()); accounts.ACCOUNTS = TMP
import pvp; pvp.ACCOUNTS, pvp.STORE = TMP, TMP / "pvp.json"
import server, matches  # noqa: F401  (server wires the modules up)
P = matches.P
fails = []


def check(name, ok, info=""):
    print(("ok  " if ok else "FAIL"), name, info)
    if not ok:
        fails.append(name)


acc = accounts.Account(9990)
spikes = frames = rallies = boss_points = 0
for seed in range(3):
    random.seed(seed)
    raw, m = matches.build_room(P.PVPRoomArgs(pvp_type=P.FIGHT_ASSOC_BOSS, pvp_args=[0, 1000201]).SerializeToString(),
                                acc, 9990, "x")
    live = m.live
    check(f"seed {seed}: club boss room plays CC rallies", live.cc)
    live.auto_skill = True
    for _ in range(11):
        frs = [P.FrameData.FromString(b) for b in live.next_rally()]
        frames += frs[-1].frame_index
        rallies += 1
        spikes += sum(1 for f in frs if f.HasField("round_data") and f.round_data.HasField("action_data")
                      and f.round_data.action_data.hit_player < 13 and f.round_data.action_data.formula_type == 5)
    boss_points += live.score[1]
check("boss scores no point", boss_points == 0, f"{boss_points}")
check("about two user spikes per rally", 1.5 <= spikes / rallies <= 2.3, f"{spikes / rallies:.2f}")
check("rallies as long as Garena's", 230 <= frames / rallies <= 340, f"{frames / rallies:.0f} frames")

import livematch
c = matches._captured("0x0302", P.PVPCreateS2C)  # a captured PvP room
check("other matches keep their rallies", not livematch.LiveMatch(c.info, 0.5).cc)
print("FAILED:" if fails else "all passed", fails or "")
raise SystemExit(1 if fails else 0)
