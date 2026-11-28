"""Hand serve power factor (engine.HAND_SERVE) as in the Garena capture dizzy2711 (27.11.2026): the serve's
rate (倍率 in power_des) was 1 without input, 0.8 for Bad (result 1) and 1.2 for Good (result 3) with the same
serve skill 10020; Nor (2) keeps 1. Offline, no accounts.
  python test_hand_serve_power.py
"""
import random

import engine

checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


check("Bad = 0.8, Nor = 1, Good = 1.2", engine.HAND_SERVE == {1: 0.8, 2: 1.0, 3: 1.2}, str(engine.HAND_SERVE))
stats = {0: {i: 5000.0 for i in range(15)}}
pw = {}
for k in (1.0, 0.8, 1.2):
    rng = random.Random(7)
    pw[k] = engine.power(stats, 0, engine.SERVE, rng, multiplier=k).ball
check("serve power scales with the factor", abs(pw[0.8] / pw[1.0] - 0.8) < 1e-6 and abs(pw[1.2] / pw[1.0] - 1.2) < 1e-6,
      f"{pw}")
print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
raise SystemExit(0 if all(checks) else 1)
