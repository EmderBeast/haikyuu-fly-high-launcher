"""Offline test of the Global / JP switch (edition.py): which tables the server loads, the fallback when the JP
tables are missing, and that cards of characters only the JP tables know are parked under "global" and back
under "jp". Runs the server modules in child processes, each with its own setting. Temporary folders only.
  python test_edition.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
checks = []
JP_ONLY = 987654  # a character id that exists in no real table


def check(name, ok, detail=""):
    checks.append(ok)
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def run(code: str, edition: str | None, accounts: Path, jp_cfg: Path | None) -> dict:
    """Runs `code` in a fresh interpreter with its own edition setting, JP folder and accounts folder."""
    env = dict(os.environ)
    env.pop("HFH_EDITION", None)
    if edition:
        env["HFH_EDITION"] = edition
    env["HFH_EDITION_FILE"] = str(accounts / "edition.json")
    env["HFH_GAMEDATA_JP"] = str(jp_cfg.parent if jp_cfg else accounts / "no-such-folder")
    pre = "; ".join(["import json, sys", "from pathlib import Path", "import accounts",
                     "accounts.ACCOUNTS = Path(%r)" % str(accounts)]) + chr(10)
    out = subprocess.run([sys.executable, "-c", pre + code], capture_output=True, text=True, env=env, cwd=str(HERE))
    if out.returncode:
        raise RuntimeError(out.stderr[-1500:])
    return json.loads(out.stdout.strip().splitlines()[-1])


INFO = """
import edition, gameconfig
print(json.dumps({"name": edition.NAME, "wanted": edition.WANTED, "fell_back": edition.FELL_BACK,
                  "cfg": str(edition.CFG), "players": len(gameconfig.PLAYERS), "knows": %d in gameconfig.PLAYERS}))
""" % JP_ONLY

GIVE = """
import gameconfig, wire
acc = accounts.Account(990801)
acc.add_card(%d); acc.save()
print(json.dumps({"cards": len(acc.cards), "parked": len(acc.parked)}))
""" % JP_ONLY

LOOK = """
import wire
acc = accounts.Account(990801)
confs = [wire.get(wire.parse(c), 3) for c in acc.cards]
sent = b"".join(p for _, p in acc.login_updates())
acc.save()
print(json.dumps({"has": %d in confs, "parked": len(acc.parked), "owned": %d in acc.owned_conf_ids(), "cards": len(acc.cards)}))
""" % (JP_ONLY, JP_ONLY)


def main():
    if not (ROOT / "gamedata" / "cfg" / "playerlist.json").exists():
        print("skipped: no gamedata/cfg")
        return True
    tmp = Path(tempfile.mkdtemp(prefix="hfh-edition-"))
    accounts_dir = tmp / "accounts"
    accounts_dir.mkdir()
    jp = tmp / "gamedata-jp" / "cfg"
    shutil.copytree(ROOT / "gamedata" / "cfg", jp)
    rows = json.loads((jp / "playerlist.json").read_text(encoding="utf-8"))
    extra = dict(rows[0])
    extra["ID"] = JP_ONLY
    (jp / "playerlist.json").write_text(json.dumps(rows + [extra]), encoding="utf-8")

    g = run(INFO, None, accounts_dir, jp)
    check("no setting: global tables", g["name"] == "global" and not g["knows"] and g["cfg"].endswith("cfg") and "gamedata-jp" not in g["cfg"])
    j = run(INFO, "jp", accounts_dir, jp)
    check("HFH_EDITION=jp: the JP tables, one character more", j["name"] == "jp" and j["knows"] and j["players"] == g["players"] + 1, f"{g['players']} -> {j['players']}")
    f = run(INFO, "jp", accounts_dir, None)
    check("jp wanted but its tables are missing: runs global and says so", f["name"] == "global" and f["wanted"] == "jp" and f["fell_back"] and not f["knows"])
    x = run(INFO, "klingon", accounts_dir, jp)
    check("unknown name: global", x["name"] == "global" and not x["fell_back"])
    (accounts_dir / "edition.json").write_text(json.dumps({"edition": "jp"}), encoding="utf-8")
    s = run(INFO, None, accounts_dir, jp)
    check("edition.json is used when the environment says nothing", s["name"] == "jp")
    e = run(INFO, "global", accounts_dir, jp)
    check("the environment wins over edition.json", e["name"] == "global")
    (accounts_dir / "edition.json").unlink()

    a = run(GIVE, "jp", accounts_dir, jp)
    check("under jp the account gets a JP-only card", a["cards"] == 1 and a["parked"] == 0)
    b = run(LOOK, "global", accounts_dir, jp)
    check("under global the card is parked: not held, not owned, not sent", not b["has"] and not b["owned"] and b["parked"] == 1 and b["cards"] == 0)
    c = run(LOOK, "global", accounts_dir, jp)
    check("it stays parked over a save under global", c["parked"] == 1 and not c["has"])
    d = run(LOOK, "jp", accounts_dir, jp)
    check("back under jp the card is there again", d["has"] and d["owned"] and d["parked"] == 0 and d["cards"] == 1)

    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL OK" if all(checks) else f"{checks.count(False)} FAILED")
    return all(checks)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
