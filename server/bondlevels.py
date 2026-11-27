"""Bond levels of a match line-up: PVPTeamInfo.formation.fetter_id_levels from the cards themselves.

Belegt (client TeamView.TeamCalculator::GetTeamFetterIdLevelsByPlayersAndType, FetterClient::get_Level, and
all 231 captured room sides that carry bonds, decoded/*match_msgs.json - same ids, order not fixed):
  - each id is a trammelproperty row (TrammelID = bond, TrammelLevel = level, one row per character with
    CharacterPropertyID; school bonds one row): 118715 = bond 1187 level 5 for 5501, 11 = school bond 1072
  - battle bonds (trammel Type 2): at least CharacterNumber of its CharacterID players among all 13 cards of
    the line-up (bench included); level = lowest star among those cards - 1, only if above 0
  - school bonds (Type 3): at least CharacterNumber cards of its school (playerlist.SchoolID = trammel.School),
    level 1
  - collection bonds (Type 1) never appear
Whether a bond is active in a rally (first seven places) is skills.active_bonds; the bond tasks (TaskType 7,
condition 71001) do not decide the level.
"""
import json

import gameconfig


def _load(name):
    if not gameconfig.AVAILABLE or not (gameconfig.CFG / f"{name}.json").exists():
        return []
    return json.loads((gameconfig.CFG / f"{name}.json").read_text(encoding="utf-8"))


def _flat(v):
    out = []
    for x in v if isinstance(v, list) else ([] if v is None else [v]):
        out += _flat(x) if isinstance(x, list) else [x]
    return out


def _num(v):
    return v if isinstance(v, int) and v > 0 else 0


ROWS: dict[tuple[int, int], list[int]] = {}
for _r in _load("trammelproperty"):
    ROWS.setdefault((_r["TrammelID"], _r["TrammelLevel"]), []).append(_r["ID"])
BONDS = [r for r in _load("trammel") if r.get("Type") in (2, 3)]


def _school(conf: int):
    row = gameconfig.PLAYERS.get(conf) or gameconfig.PLAYERS.get(conf % 10000) or {}
    return row.get("SchoolID")


def fetter_ids(players) -> list[int]:
    """players: (conf_id, star) of the line-up's cards (all of them)."""
    players = [(c, s) for c, s in players if c]
    out = []
    for bond in BONDS:
        need = _num(bond.get("CharacterNumber"))
        if bond["Type"] == 3:
            stars = [s for c, s in players if _school(c) == bond.get("School")]
        else:
            chars = set(_flat(bond.get("CharacterID")))
            stars = [s for c, s in players if c % 10000 in chars]
        if not need or len(stars) < need:
            continue
        level = 1 if bond["Type"] == 3 else min(stars) - 1
        if level > 0:
            out += ROWS.get((bond["ID"], level), [])
    return out


def set_team(team):
    """Fills team.formation.fetter_id_levels from its players."""
    del team.formation.fetter_id_levels[:]
    team.formation.fetter_id_levels.extend(
        fetter_ids([(p.info.conf_id, p.info.star) for p in team.formation.players]))
