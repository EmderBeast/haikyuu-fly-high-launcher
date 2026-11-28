"""
Admin Mode: load the four best teams of the Fly High Hub ("Beste Teams": against Receive, Power, Quick and Block,
JP = every character, Global = the Global characters) 1:1 into an account (Emre 28.11.2026).

Data: data/best_teams.json, written by tools/hub_best_teams.js from the Hub's src/ui.js (BEST_B_<JP|G>_<R|P|Q|B>).
Queue entry {"best_teams": {"edition": "jp" | "global", "focus": "receive" | "power" | "quick" | "block"}},
in-game command /bestteams jp|global [receive|power|quick|block].

What is loaded (through scanimport.apply, the HFH-Bot scan import):
  - every player of the four teams (court, libero, bench): level, top star, skill levels and resonance, memory
    (tier as in the Hub, max level), six potentials (set, place, level, main stat, sub stat rolls);
  - one coach per team with the Hub's Positional Advantages (coach card in that line-up);
  - line-ups 0-3 ("<Edition> vs <Specialty>"): court positions 1-6 in rotation order (inst_ids 0-5, front row 2-4),
    libero (6), bench (7-12; the Hub's substitutes are on it, subbing in is done in the match), libero replacement
    as in the Hub, Lineup Specialty that counters the opponent's (skills.COUNTERS), three cheers as in the Hub
    (Kiyoko Sportswear, Shiratorizawa, Date Kogyo).
A character can wear only one build: where the four teams build the same character differently, the build of
the "focus" team counts (default receive); all four line-ups are still set. The line-ups the account had before
are kept in admin_flags["best_teams_prev"] (put back with {"best_teams": "off"}).
"""
import json
from pathlib import Path

import wire

DATA = Path(__file__).parent / "data" / "best_teams.json"
OPPS = ["receive", "power", "quick", "block"]
NAMES = {"receive": "Receive", "power": "Power", "quick": "Quick", "block": "Block"}
LIBERO_FOR = {"WS": 1, "OP": 2, "MB": 3, "S": 4}  # LIBERO_REPLACE_TYPE
TEAM_SIZE = 13
# coaches of the Hub the game tables do not have yet -> the one the Hub names as next best with the same PAs
# (its note on the JP teams: Kurosu best, Yamiji -2.1 %)
COACH_STANDIN = {"Norimune Kurosu": "Takeyuki Yamiji"}


def load() -> dict:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_args(args: list[str]) -> dict | None:
    """/bestteams jp|global [focus] | off -> the queue value, None for bad arguments."""
    a = [x.lower() for x in args]
    if a[:1] in (["off"], ["aus"]):
        return {"off": True}
    ed = {"jp": "jp", "japan": "jp", "global": "global", "gl": "global", "g": "global"}.get(a[0]) if a else None
    if ed is None:
        return None
    focus = a[1] if len(a) > 1 else "receive"
    focus = next((o for o in OPPS if o.startswith(focus)), None)
    return {"edition": ed, "focus": focus} if focus else None


def _team_players(t: dict) -> list[dict]:
    return [p for p in t["court"] + [t.get("libero")] + t.get("bench", []) if p]


def scan_of(teams: list[dict], focus: str) -> dict:
    """The four teams as one HFH-Bot scan: the focus team last, so its builds win."""
    order = sorted(teams, key=lambda t: t["opp"] == focus)
    players = {}
    for t in order:
        for p in _team_players(t):
            players.pop(p["id"], None)
            players[p["id"]] = {"id": p["id"], "lv": p.get("lv") or 80, "stars": p.get("stars") or 6,
                                "skills": p.get("skills") or {}, "memory": p.get("memory"), "pots": p.get("pots") or []}
    coaches = [_coach(t["coach"]) for t in teams if t.get("coach")]
    return {"hfhscan": 1, "players": list(players.values()), "coaches": coaches}


def _coach(c: dict) -> dict:
    import scanimport
    if scanimport.coach_conf(c.get("name"), c.get("lv")) is None and c.get("name") in COACH_STANDIN:
        return dict(c, name=COACH_STANDIN[c["name"]], hub_name=c["name"])
    return c


def _best_inst(acc) -> dict[int, int]:
    import admin
    by_conf = {}
    for inst, card in admin._all_cards(acc).items():
        f = wire.parse(card)
        conf, level = wire.get(f, 3, 0), wire.get(f, 4, 0)
        if conf not in by_conf or level > by_conf[conf][1]:
            by_conf[conf] = (inst, level)
    return {c: v[0] for c, v in by_conf.items()}


def formation(t: dict, lineup: int, inst: dict, coach_inst: int):
    import matches
    import scanimport
    P = matches.P
    f = P.TeamFormation(lineup=lineup)
    ids = [inst.get(p["id"], 0) if p else 0 for p in t["court"]]
    ids.append(inst.get(t["libero"]["id"], 0) if t.get("libero") else 0)
    ids += [inst.get(p["id"], 0) for p in t.get("bench", [])][:TEAM_SIZE - 7]
    ids += [0] * (TEAM_SIZE - len(ids))
    f.inst_ids.extend(ids)
    f.libero_replace_type = LIBERO_FOR.get(t.get("liberoFor") or "", 0)
    if coach_inst:
        f.coach_card_inst_id = coach_inst
    cheers = [scanimport.CHEERS[c] for c in t.get("cheers") or [] if c in scanimport.CHEERS]
    f.player_supports.extend((cheers + [0, 0, 0])[:3])
    f.genre.value = int(t.get("genre", 4))
    return f


def apply(acc, value) -> list[str]:
    import matches
    import scanimport
    P = matches.P
    if value == "off" or (isinstance(value, dict) and value.get("off")):
        prev = acc.admin_flags.pop("best_teams_prev", None)
        if prev is None:
            return ["Bestteams: nichts zurückzunehmen"]
        acc.team = bytes.fromhex(prev) if prev else None
        acc.admin_flags.pop("best_teams", None)
        acc.save()
        return ["Bestteams: alte Aufstellungen zurück (Spieler, Potentials und Coaches bleiben)"]
    if not isinstance(value, dict):
        value = {"edition": str(value)}
    ed = "jp" if str(value.get("edition", "")).lower() == "jp" else "global"
    focus = value.get("focus") if value.get("focus") in OPPS else "receive"
    data = load()
    teams = (data.get("teams") or {}).get(ed) or []
    if len(teams) != 4:
        return [f"Bestteams: keine Daten für {ed} (data/best_teams.json fehlt, tools/hub_best_teams.js laufen lassen)"]
    report = scanimport.apply(acc, scan_of(teams, focus))
    coaches = (acc.admin_flags.get("scan_import") or {}).get("coaches", [])
    with_coach = [t for t in teams if t.get("coach")]
    coach_of = {t["opp"]: c for t, c in zip(with_coach, coaches)} if len(coaches) == len(with_coach) else {}
    inst = _best_inst(acc)
    if "best_teams_prev" not in acc.admin_flags:
        acc.admin_flags["best_teams_prev"] = acc.team.hex() if acc.team else ""
    info = P.TeamInfoS2C()
    info.ParseFromString(acc.team_info())
    keep = [P.TeamFormation.FromString(f.SerializeToString()) for f in info.formations if f.lineup > 3]
    del info.formations[:]
    tag = "JP" if ed == "jp" else "Global"
    for i, t in enumerate(sorted(teams, key=lambda t: OPPS.index(t["opp"]))):
        info.formations.append(formation(t, i, inst, coach_of.get(t["opp"], 0)))
    info.formations.extend(keep)
    names = [P.FormationName(lineup=n.lineup, name=n.name) for n in info.formation_name_sli if n.lineup > 3]
    del info.formation_name_sli[:]
    for i, o in enumerate(OPPS):
        info.formation_name_sli.add(lineup=i, name=f"{tag} vs {NAMES[o]}")
    info.formation_name_sli.extend(names)
    acc.team = info.SerializeToString()
    acc.admin_flags["best_teams"] = {"edition": ed, "focus": focus, "sim": data.get("simVersion", "")}
    acc.save()
    standin = sorted({f"{c['hub_name']} -> {c['name']}" for c in (_coach(t["coach"]) for t in with_coach) if c.get("hub_name")})
    if standin:
        report.append("Bestteams: Coach gibt es im Spiel noch nicht, ersetzt (wie im Hub empfohlen): " + ", ".join(standin))
    missing = sorted({p["id"] for t in teams for p in _team_players(t) if p["id"] not in inst})
    report.append(f"Bestteams {tag} (Hub {data.get('simVersion', '?')}): Aufstellungen 0-3 = vs Receive, Power, Quick, "
                  f"Block; Builds wie im Team vs {NAMES[focus]}" + (f"; fehlende Spieler: {missing}" if missing else ""))
    return report
