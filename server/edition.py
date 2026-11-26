"""
Which game data the server runs on: "global" (the Garena tables) or "jp" (the Garena tables plus the
content of the Japanese version that the patched client knows).

The client stays the Garena client either way. For "jp" the launcher's proxy hands it a patched table
bundle (cfg.ab) and the extra asset bundles; the server must then read the very same tables, or it would
not know the new cards and skills. So the edition is a property of the running server (the tables are
loaded once at start), not of an account: switching means writing the setting and restarting the server -
and starting the client through the proxy with or without the patched bundles.

Setting, first match wins:
  1. environment HFH_EDITION = global | jp
  2. server/edition.json {"edition": "global" | "jp"}   (written by set(), e.g. from the launcher)
  3. "global"

Data per edition (both ignored by git, made with tools_config_dump.py / typed_cfg.py from a cfg.ab):
  global: gamedata/cfg            and decoded/analysis/typed
  jp:     gamedata-jp/cfg         and gamedata-jp/typed   (another folder with HFH_GAMEDATA_JP)
If "jp" is chosen but gamedata-jp/cfg is missing, the server falls back to the global data and says so
(NAME stays "global", FELL_BACK is True), so a half set-up PC still starts.

Accounts are shared between the editions. Cards whose character the current tables do not know (JP-only
cards while the server runs "global") are parked: kept in the account file, but not sent to the client and
not usable, and back again under "jp" (accounts.Account).
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
FILE = Path(os.environ.get("HFH_EDITION_FILE") or Path(__file__).parent / "edition.json")
EDITIONS = ("global", "jp")


def wanted() -> str:
    """The edition asked for (environment, then edition.json); "global" for anything unknown."""
    name = (os.environ.get("HFH_EDITION") or "").strip().lower()
    if not name:
        try:
            name = str(json.loads(FILE.read_text(encoding="utf-8")).get("edition") or "").strip().lower()
        except (OSError, ValueError, AttributeError):
            name = ""
    return name if name in EDITIONS else "global"


def gamedata(name: str) -> Path:
    if name == "jp":  # HFH_GAMEDATA_JP: the JP data somewhere else (e.g. the folder the client bundles are built in)
        return Path(os.environ.get("HFH_GAMEDATA_JP") or ROOT / "gamedata-jp")
    return ROOT / "gamedata"


def typed(name: str) -> Path:
    return gamedata(name) / "typed" if name == "jp" else ROOT / "decoded" / "analysis" / "typed"


def available(name: str) -> bool:
    return (gamedata(name) / "cfg" / "playerlist.json").exists()


def set(name: str) -> str:
    """Store the edition for the next server start; returns what was stored."""
    name = name.strip().lower()
    if name not in EDITIONS:
        raise ValueError(f"unknown edition {name!r}, expected one of {EDITIONS}")
    FILE.write_text(json.dumps({"edition": name}), encoding="utf-8")
    return name


WANTED = wanted()
FELL_BACK = WANTED != "global" and not available(WANTED)
NAME = "global" if FELL_BACK else WANTED
GAMEDATA = gamedata(NAME)
CFG = GAMEDATA / "cfg"
TYPED = typed(NAME)


if __name__ == "__main__":  # python edition.py [global|jp]: show or store the edition
    import sys
    if len(sys.argv) > 1:
        print("stored for the next server start:", set(sys.argv[1]))
    else:
        print(f"edition {NAME}" + (f" (wanted {WANTED}, but {gamedata(WANTED) / 'cfg'} is missing)" if FELL_BACK else "")
              + f", tables {CFG}")
