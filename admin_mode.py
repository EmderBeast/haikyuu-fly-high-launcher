"""
Admin Mode window of the launcher (admin build only).

Writes the wanted changes to server/accounts/admin_queue/<gid>.json and restarts
the game; the server applies them at the next login (server/admin.py) and reports
what it did in server/accounts/admin_done/<gid>.json.
"""
import json
import re
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import launcher_i18n as i18n

BG = "#1a1a2e"
PANEL = "#16213e"
FG = "#eeeeee"
GOLD = "#f5c518"

CURRENCIES = [(100, "Gold", 9_999_999), (108, "Diamanten", 999_999), (102, "ERF", 9_999_999),
              (105, "Ausdauer (Onigiri)", 999)]
RARITY = {1: "Stufe 1 (max Lv. 6)", 2: "Stufe 2 (max Lv. 6)", 3: "Stufe 3 (max Lv. 9)",
          4: "Stufe 4 (max Lv. 12)"}
MAX_LEVEL = {1: 6, 2: 6, 3: 9, 4: 12}
POSITIONS = [0, 1, 2, 3, 4, 5]
COACH_JOBS = {1: 20074475, 2: 680108, 3: 643604, 4: 32911123, 5: 814991}  # job -> language id (server/admin.py)
COACH_BUFF_LEVELS = [3, 6, 9, 12, 15]  # coachconstant 2: one job buff per reached level


class GameData:
    """Names and potential tables from gamedata/cfg (German texts from language.json)."""

    def __init__(self, base: Path):
        cfg = base / "gamedata" / "cfg"
        load = lambda n: json.loads((cfg / f"{n}.json").read_text(encoding="utf-8"))
        lang = {r["ID"]: r for r in load("language")}

        def de(ref):
            m = re.findall(r"\d+", str(ref))
            row = lang.get(int(m[0])) if m else None
            first, second = ("English", "German") if i18n.LANG == "en" else ("German", "English")
            return (row.get(first) or row.get(second)) if row else None

        self.suits = {}
        for r in load("specialtysuitconf"):
            self.suits[r["ID"]] = de(r["Name"]) or f"Set {r['ID']}"
        self.attr = {}
        for r in load("specialtyattrconf"):
            if r["AttrID"] not in self.attr:
                name = (de(r.get("Des")) or str(r["AttrID"])).replace("+{0}", "").replace("{0}", "").strip()
                if r.get("Attr") == 200:  # percentage variant
                    name += " %"
                self.attr[r["AttrID"]] = name
        self.vice_pools = {r["ID"]: r["Viceattr"] for r in load("specialtyviceattr")}
        self.card_names = {r["ID"]: (de(r.get("Intro")) or str(r["ID"])) for r in load("playerlist")}
        self.specialty = {}  # (suit, rare, position) -> row
        for r in load("specialty"):
            self.specialty[(r["SuitID"], r["Rare"], r.get("Position") or 0)] = r
        # coaches (server/coach.py): id -> row, buff id -> text with its numbers, top level per rarity
        self.coaches, self.coach_buffs, self.coach_top = {}, {}, {}
        try:
            for r in load("coachlist"):
                self.coaches[r["ID"]] = dict(r, name=f"{de(r.get('CoachName')) or r['ID']} ★{r.get('CoachRare')}")
            for r in load("coachbuff"):
                text = de(r.get("BuffName")) or str(r["ID"])
                for i, val in enumerate(str(r.get("BuffNumber") or "").split("_")):
                    text = text.replace("{%d}" % i, val)
                self.coach_buffs[r["ID"]] = dict(r, text=text)
            for r in load("coachlevel"):
                self.coach_top[r["CoachRare"]] = max(self.coach_top.get(r["CoachRare"], 0), r["Level"])
            for r in load("coachconstant"):
                if r["ID"] == 2 and r.get("Data2"):
                    COACH_BUFF_LEVELS[:] = [int(x) for x in str(r["Data2"]).split(",") if x.strip()]
        except Exception:
            pass  # older game data without coaches: the tab stays empty
        self.jobs = {j: (de(lid) or str(j)).replace("- ", "") for j, lid in COACH_JOBS.items()}

    def attr_label(self, attr_id):
        return f"{self.attr.get(attr_id, attr_id)} ({attr_id})"

    @staticmethod
    def id_of(label):
        m = re.search(r"\((\d+)\)\s*$", label or "")
        return int(m.group(1)) if m else None

    def card_name(self, conf):
        return self.card_names.get(conf) or f"Karte {conf}"

    def mains(self, row):
        m = row["MainattrID"]
        return m if isinstance(m, list) else [m]

    def vices(self, row):
        return self.vice_pools.get(row.get("ViceattrPool"), [])


class TrVar(tk.StringVar):
    """StringVar whose texts are shown in the UI language."""

    def set(self, value):
        super().set(i18n.tr(value))


BEST_CHOICES = {"nicht laden": None, "JP-Bestteams": "jp", "Global-Bestteams": "global",
                "alte Aufstellungen zurück": "off"}
RESO_CHOICES = {"unverändert": None, "aus": 0, "1. Stufe": "t1", "2. Stufe": "t2", "3. Stufe": "t3",
                "4. Stufe": "t4", "5. Stufe": "t5", "max": "max"}  # skill resonance tiers of a card
ALL = "*"  # target "all my accounts"
ALL_LABEL = "Alle meine Konten"


class AdminWindow:
    def __init__(self, launcher):
        if i18n.LANG != "de":  # rarity names are compared with the box's value: translate once
            for k in list(RARITY):
                RARITY[k] = i18n.tr(RARITY[k])
        self.l = launcher
        self.base = launcher.base
        self.data = GameData(self.base)
        self.pending_specs = []
        self.pending_power = {}
        self.pending_reso = {}
        self.pending_coaches = []

        self.win = tk.Toplevel(launcher.root)
        self.win.title("Admin Mode")
        self.win.geometry("760x640")
        self.win.configure(bg=BG)
        if launcher.logo:
            self.win.iconphoto(False, launcher.logo)

        top = tk.Frame(self.win, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(top, text="Admin Mode", bg=BG, fg=GOLD, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(top, text="Konto:", bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side="left", padx=(24, 4))
        self.gid = tk.StringVar()  # the chosen account's gid (the box shows "name (gid)")
        gids = sorted(self.unlocked())
        names = self.players()
        self.gid_labels = {i18n.tr(ALL_LABEL): ALL}
        self.gid_labels.update({(f"{names[g]} ({g})" if names.get(g) else g): g for g in gids})
        self.gid_label = tk.StringVar()
        self.gid_box = ttk.Combobox(top, textvariable=self.gid_label, values=list(self.gid_labels),
                                    state="readonly", width=30)
        self.gid_box.pack(side="left")
        self.gid_box.bind("<<ComboboxSelected>>", self.account_chosen)
        if gids:  # default: all own accounts, so it does not matter which Garena login the game uses
            self.gid.set(ALL)
            self.gid_label.set(next(iter(self.gid_labels)))
        self.login_var = TrVar(value=self.last_login_text())
        tk.Label(self.win, textvariable=self.login_var, bg=BG, fg="#aaaaaa", font=("Segoe UI", 9)).pack(
            anchor="w", padx=14)

        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=14, pady=6)
        nb.add(self.tab_account(nb), text=i18n.tr("Konto"))
        nb.add(self.tab_cards(nb), text=i18n.tr("Charaktere & Gegenstände"))
        nb.add(self.tab_potentials(nb), text=i18n.tr("Potentials"))
        nb.add(self.tab_power(nb), text=i18n.tr("Stärke"))
        if self.data.coaches:
            nb.add(self.tab_coach(nb), text=i18n.tr("Trainer"))
        if self.shared():
            nb.add(self.tab_rights(nb), text=i18n.tr("Rechte"))

        bottom = tk.Frame(self.win, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(bottom, text="Übernehmen & Spiel neu starten", command=self.apply, bg="#0f3460",
                  fg="white", font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2",
                  activebackground="#1a5276", activeforeground="white").pack(side="left")
        tk.Button(bottom, text="Admin Mode sperren", command=self.lock, bg="#53354a", fg="white",
                  font=("Segoe UI", 10), relief="flat", cursor="hand2").pack(side="right")
        self.status = TrVar(value=i18n.tr(self.last_report()))
        tk.Label(self.win, textvariable=self.status, bg=BG, fg="#9be7a5", font=("Segoe UI", 9),
                 wraplength=720, justify="left").pack(fill="x", padx=14, pady=(0, 10))
        i18n.translate_tree(self.win)

    # --- files --------------------------------------------------------------------

    def account_chosen(self, _event=None):
        self.gid.set(self.gid_labels.get(self.gid_label.get(), ""))
        if hasattr(self, "status"):
            self.status.set(self.last_report())

    def target_gid(self) -> str:
        """The single account behind the choice ("all my accounts": the one that logged in last)."""
        gid = self.gid.get()
        if gid != ALL:
            return gid
        last = self.last_login().get("gid", "")
        return last if last in self.unlocked() else next(iter(sorted(self.unlocked())), "")

    def last_login(self) -> dict:
        try:
            return json.loads((self.base / "server" / "accounts" / "last_login.json").read_text("utf-8"))
        except Exception:
            return {}

    def last_login_text(self) -> str:
        d = self.last_login()
        if not d.get("gid"):
            return "Zuletzt eingeloggt: noch niemand"
        name = self.players().get(d["gid"], "")
        who = f"{name} ({d['gid']})" if name else d["gid"]
        return "Zuletzt eingeloggt: " + who + ", " + time.strftime("%d.%m. %H:%M", time.localtime(d["time"]))

    def unlocked(self):
        """Accounts to edit: admins; on an own server every account (all belong to the PC's owner),
        on a shared server every player the server knows."""
        acc_dir = self.base / "server" / "accounts"
        out = {}
        try:
            out.update(json.loads((acc_dir / "admin_unlocked.json").read_text("utf-8")))
        except Exception:
            pass
        st = self.admins()
        if st.get("owner"):
            out[str(st["owner"])] = "owner"
        out.update({g: "admin" for g in st["admins"]})
        if self.shared() or out:  # own server: unlocked once = all accounts on it
            out.update({g: n for g, n in self.players().items() if g not in out})
        return out

    def shared(self):
        import os
        return (self.base / "server" / "multiplayer.json").exists() or \
            os.environ.get("HFH_MULTIPLAYER", "") not in ("", "0")

    def players(self):
        """gid -> name from the server's account files and PvP snapshots."""
        acc_dir = self.base / "server" / "accounts"
        out = {}
        for f in acc_dir.glob("*.json"):
            gid = f.stem.split("-")[0]
            if gid.isdigit():
                out[gid] = ""
        try:
            pvp = json.loads((acc_dir / "pvp.json").read_text("utf-8"))
            for g, snap in pvp.get("snapshots", {}).items():
                out[g] = snap.get("name", "") or out.get(g, "")
        except Exception:
            pass
        return out

    def admins_file(self):
        return self.base / "server" / "accounts" / "admins.json"

    def admins(self):
        try:
            st = json.loads(self.admins_file().read_text("utf-8"))
        except Exception:
            st = {}
        st.setdefault("owner", None)
        st.setdefault("admins", {})
        return st

    def last_report(self):
        try:
            d = json.loads((self.base / "server" / "accounts" / "admin_done" / f"{self.target_gid()}.json")
                           .read_text("utf-8"))
            return "Zuletzt übernommen (" + time.strftime("%d.%m. %H:%M", time.localtime(d["time"])) + "): " \
                + ", ".join(d["done"])
        except Exception:
            return "Änderungen werden beim nächsten Login übernommen."

    # --- tabs -----------------------------------------------------------------------

    def frame(self, nb):
        f = tk.Frame(nb, bg=PANEL)
        return f

    def tab_account(self, nb):
        f = self.frame(nb)
        self.level_on = tk.BooleanVar()
        self.level = tk.IntVar(value=60)
        row = tk.Frame(f, bg=PANEL)
        row.pack(anchor="w", padx=12, pady=(14, 6))
        tk.Checkbutton(row, text="Manager-Level setzen:", variable=self.level_on, bg=PANEL, fg=FG,
                       selectcolor=BG, activebackground=PANEL).pack(side="left")
        tk.Spinbox(row, from_=1, to=60, textvariable=self.level, width=5).pack(side="left", padx=6)

        tk.Label(f, text="Währungen (Haken = auf diesen Wert setzen):", bg=PANEL, fg=GOLD,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        self.cur = {}
        for cid, name, default in CURRENCIES:
            r = tk.Frame(f, bg=PANEL)
            r.pack(anchor="w", padx=24, pady=2)
            on, val = tk.BooleanVar(), tk.StringVar(value=str(default))
            tk.Checkbutton(r, text=name, variable=on, width=20, anchor="w", bg=PANEL, fg=FG,
                           selectcolor=BG, activebackground=PANEL).pack(side="left")
            tk.Entry(r, textvariable=val, width=14).pack(side="left")
            self.cur[cid] = (on, val)

        self.stamina = tk.StringVar(value="keep")
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(16, 4))
        tk.Label(r, text="Unendlich Ausdauer:", bg=PANEL, fg=FG).pack(side="left")
        for text, value in (("unverändert", "keep"), ("an", "on"), ("aus", "off")):
            tk.Radiobutton(r, text=text, value=value, variable=self.stamina, bg=PANEL, fg=FG,
                           selectcolor=BG, activebackground=PANEL).pack(side="left", padx=4)
        return f

    def tab_cards(self, nb):
        f = self.frame(nb)
        self.all_cards = tk.BooleanVar()
        self.max_cards = tk.BooleanVar()
        self.items_on = tk.BooleanVar()
        self.items = tk.IntVar(value=999)
        # "unlock everything" (server/unlockall.py): all kinds at once, or only cheers / cosmetics
        self.unlock_all = tk.BooleanVar()
        self.unlock_cheers = tk.BooleanVar()
        self.unlock_cosmetics = tk.BooleanVar()
        tk.Checkbutton(f, text="ALLES freischalten: alle Charaktere und Memories auf max, Gegenstände, Währungen, "
                               "alle Trainer, perfekte Potentials, alle Cheers und die ganze Kosmetik",
                       variable=self.unlock_all, bg=PANEL, fg=GOLD, selectcolor=BG, activebackground=PANEL,
                       wraplength=680, justify="left", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(14, 2))
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=36)
        tk.Label(r, text="oder nur:", bg=PANEL, fg="#aaaaaa").pack(side="left")
        tk.Checkbutton(r, text="alle Cheers (höchster Stern)", variable=self.unlock_cheers, bg=PANEL, fg=FG,
                       selectcolor=BG, activebackground=PANEL).pack(side="left", padx=6)
        tk.Checkbutton(r, text="alle Kosmetik (Porträts, Rahmen, Titel, Hintergründe …)", variable=self.unlock_cosmetics,
                       bg=PANEL, fg=FG, selectcolor=BG, activebackground=PANEL).pack(side="left", padx=6)
        for var, text in ((self.all_cards, "Alle Charaktere und Memories freischalten (je eins von jedem, das du noch nicht hast)"),
                          (self.max_cards, "Alles auf max: Karten 6 Sterne / Level 80, volle Skill-Resonance, Memories Level 80 / Tier V")):
            tk.Checkbutton(f, text=text, variable=var, bg=PANEL, fg=FG, selectcolor=BG,
                           activebackground=PANEL, wraplength=680, justify="left").pack(anchor="w", padx=12, pady=(14, 2))
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(14, 2))
        tk.Checkbutton(r, text="Alle Gegenstände auf Anzahl:", variable=self.items_on, bg=PANEL, fg=FG,
                       selectcolor=BG, activebackground=PANEL).pack(side="left")
        tk.Spinbox(r, from_=0, to=99999, textvariable=self.items, width=7).pack(side="left", padx=6)
        tk.Label(f, text="Alle Sorten aus dem normalen Inventar (Tickets, Material, Geschenke …).",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9)).pack(anchor="w", padx=36)
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(14, 2))
        tk.Label(r, text="Skill-Resonance aller Karten:", bg=PANEL, fg=FG).pack(side="left")
        self.reso_all = TrVar(value=i18n.tr("unverändert"))
        ttk.Combobox(r, textvariable=self.reso_all, values=[i18n.tr(k) for k in RESO_CHOICES], state="readonly",
                     width=14).pack(side="left", padx=6)
        tk.Label(f, text="Stufen je Karte (4/6/8/10/12 Skill-Level). Einzelne Karten: Tab „Stärke“.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9)).pack(anchor="w", padx=36)

        # a scan file of the HFH-Bot (made for the CC simulator / Fly High Hub): the scanned account here
        self.pending_scan = None
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(18, 2))
        tk.Button(r, text="Scan vom HFH-Bot laden …", command=self.load_scan, bg="#0f3460", fg="white",
                  relief="flat").pack(side="left")
        tk.Button(r, text="Scan entfernen", command=self.clear_scan, bg="#53354a", fg="white",
                  relief="flat").pack(side="left", padx=6)
        self.scan_info = TrVar(value=i18n.tr("Kein Scan gewählt."))
        tk.Label(f, textvariable=self.scan_info, bg=PANEL, fg="#00ff88", font=("Segoe UI", 9), wraplength=680,
                 justify="left").pack(anchor="w", padx=36)
        tk.Label(f, text="Übernimmt Spieler (Level, Sterne, Skills, Memory, Potentials), Potentials und Memories im "
                         "Inventar und Coaches aus der Scan-Datei (HFH-Bot → scans). Ein neuer Scan ersetzt den alten.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9), wraplength=680, justify="left").pack(anchor="w", padx=36)

        # the Fly High Hub's four best teams 1:1 (server/bestteams.py, data/best_teams.json)
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(18, 2))
        tk.Label(r, text="Bestteams aus dem Fly High Hub:", bg=PANEL, fg=GOLD,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.best_teams = TrVar(value=i18n.tr("nicht laden"))
        ttk.Combobox(r, textvariable=self.best_teams, values=[i18n.tr(k) for k in BEST_CHOICES], state="readonly",
                     width=24).pack(side="left", padx=6)
        tk.Label(r, text="Builds wie gegen:", bg=PANEL, fg=FG).pack(side="left", padx=(10, 0))
        self.best_focus = tk.StringVar(value="Receive")
        ttk.Combobox(r, textvariable=self.best_focus, values=["Receive", "Power", "Quick", "Block"], state="readonly",
                     width=9).pack(side="left", padx=6)
        tk.Label(f, text="Lädt die 4 Bestteams (gegen Receive, Power, Quick, Block) 1:1 wie im Hub: Spieler mit Skills, "
                         "Memories und Potentials, je Team ein Coach mit den Positional Advantages, Aufstellungen 0-3 "
                         "mit Libero, Bank und Cheers. Ist ein Spieler in mehreren Teams verschieden gebaut, gilt der "
                         "Build aus dem Team rechts. „Alte Aufstellungen zurück“ stellt deine vorigen Aufstellungen her.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9), wraplength=680, justify="left").pack(anchor="w", padx=36)
        return f

    def load_scan(self):
        from tkinter import filedialog
        start = Path.home() / "Downloads" / "HFH-Bot" / "HFH-Bot" / "scans"
        path = filedialog.askopenfilename(parent=self.win, title=i18n.tr("Scan vom HFH-Bot laden …"),
                                          initialdir=str(start) if start.exists() else str(Path.home()),
                                          filetypes=[("HFH-Bot Scan", "*.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or int(data.get("hfhscan") or 0) != 1:
                raise ValueError(i18n.tr("Keine Scan-Datei des HFH-Bots"))
        except Exception as e:
            messagebox.showerror("Admin Mode", f"{Path(path).name}: {e}", parent=self.win)
            return
        self.pending_scan = data
        parts = [f"{len(data.get(k) or [])} {i18n.tr(n)}" for k, n in
                 (("players", "Spieler"), ("pots", "Potentials"), ("memoryCopies", "Memories"), ("coaches", "Coaches"))
                 if data.get(k)]
        self.scan_info.set(f"{Path(path).name} ({data.get('date', '?')}): " + ", ".join(parts))

    def clear_scan(self):
        self.pending_scan = None
        self.scan_info.set(i18n.tr("Kein Scan gewählt."))

    def tab_potentials(self, nb):
        f = self.frame(nb)
        d = self.data
        self.perfect = tk.StringVar(value="keep")
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(12, 0))
        tk.Label(r, text="Perfekte Potentials nach Guide (alle Karten):", bg=PANEL, fg=GOLD,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        for text, value in (("unverändert", "keep"), ("an", "on"), ("aus", "off")):
            tk.Radiobutton(r, text=text, value=value, variable=self.perfect, bg=PANEL, fg=FG,
                           selectcolor=BG, activebackground=PANEL).pack(side="left", padx=4)
        tk.Label(f, text="Jede Karte trägt die Sets und Werte aus ihrem Potential-Guide (max). Neue Karten bekommen "
                         "sie beim nächsten Login. Aus = die alten Potentials kommen zurück.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9), wraplength=680, justify="left").pack(anchor="w", padx=12)
        grid = tk.Frame(f, bg=PANEL)
        grid.pack(anchor="w", padx=12, pady=(12, 4))

        def label(r, text):
            tk.Label(grid, text=text, bg=PANEL, fg=FG).grid(row=r, column=0, sticky="w", pady=2)

        self.suit = tk.StringVar()
        self.rare = tk.StringVar(value=RARITY[4])
        self.pos = tk.StringVar(value=i18n.tr("Platz 1"))
        self.plevel = tk.IntVar(value=12)
        self.main = tk.StringVar()
        self.count = tk.IntVar(value=1)
        suits = [f"{n} ({i})" for i, n in d.suits.items()]
        label(0, "Set:")
        cb = ttk.Combobox(grid, textvariable=self.suit, values=suits, state="readonly", width=34)
        cb.grid(row=0, column=1, columnspan=3, sticky="w")
        self.suit.set(suits[0])
        label(1, "Seltenheit:")
        ttk.Combobox(grid, textvariable=self.rare, values=list(RARITY.values()), state="readonly",
                     width=20).grid(row=1, column=1, sticky="w")
        label(2, "Platz:")
        ttk.Combobox(grid, textvariable=self.pos, values=[i18n.tr(f"Platz {p + 1}") for p in POSITIONS],
                     state="readonly", width=10).grid(row=2, column=1, sticky="w")
        label(3, "Level:")
        tk.Spinbox(grid, from_=0, to=12, textvariable=self.plevel, width=5).grid(row=3, column=1, sticky="w")
        label(4, "Hauptwert:")
        self.main_box = ttk.Combobox(grid, textvariable=self.main, state="readonly", width=30)
        self.main_box.grid(row=4, column=1, columnspan=3, sticky="w")
        label(5, "Nebenwerte:")
        self.vice_vars = []
        self.vice_boxes = []
        for i in range(4):
            v, lv = tk.StringVar(), tk.IntVar(value=0)
            box = ttk.Combobox(grid, textvariable=v, state="readonly", width=30)
            box.grid(row=5 + i, column=1, columnspan=2, sticky="w", pady=1)
            tk.Label(grid, text="Stufe +", bg=PANEL, fg=FG).grid(row=5 + i, column=3, sticky="e")
            tk.Spinbox(grid, from_=0, to=5, textvariable=lv, width=4).grid(row=5 + i, column=4, sticky="w")
            self.vice_vars.append((v, lv))
            self.vice_boxes.append(box)
        label(9, "Anzahl:")
        tk.Spinbox(grid, from_=1, to=50, textvariable=self.count, width=5).grid(row=9, column=1, sticky="w")
        for var in (self.suit, self.rare, self.pos):
            var.trace_add("write", lambda *_: self.refresh_attrs())
        self.refresh_attrs()

        btns = tk.Frame(f, bg=PANEL)
        btns.pack(anchor="w", padx=12, pady=6)
        tk.Button(btns, text="Hinzufügen", command=self.add_spec, bg="#0f3460", fg="white",
                  relief="flat").pack(side="left")
        tk.Button(btns, text="Ganzes Set (alle 6 Plätze, max)", command=self.add_full_set, bg="#0f3460",
                  fg="white", relief="flat").pack(side="left", padx=6)
        tk.Button(btns, text="Liste leeren", command=self.clear_specs, bg="#53354a", fg="white",
                  relief="flat").pack(side="left")
        self.spec_list = tk.Listbox(f, height=7, bg="#0f0f23", fg="#00ff88", font=("Consolas", 9))
        self.spec_list.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        return f

    # --- potentials -----------------------------------------------------------------

    def current_row(self):
        suit = GameData.id_of(self.suit.get())
        rare = next((k for k, v in RARITY.items() if v == self.rare.get()), 4)
        pos = int(self.pos.get().split()[-1]) - 1
        return self.data.specialty.get((suit, rare, pos)), rare

    def refresh_attrs(self):
        row, rare = self.current_row()
        if not row:
            return
        mains = [self.data.attr_label(a) for a in self.data.mains(row)]
        self.main_box.configure(values=mains)
        if self.main.get() not in mains:
            self.main.set(mains[0])
        vices = [""] + [self.data.attr_label(a) for a in self.data.vices(row)]
        for i, box in enumerate(self.vice_boxes):
            box.configure(values=vices)
            if not self.vice_vars[i][0].get() or self.vice_vars[i][0].get() not in vices:
                self.vice_vars[i][0].set(vices[i + 1] if i + 1 < len(vices) else "")
        self.plevel.set(min(self.plevel.get(), MAX_LEVEL[rare]) or MAX_LEVEL[rare])

    def add_spec(self):
        row, rare = self.current_row()
        if not row:
            messagebox.showerror("Admin Mode", "Diese Kombination gibt es im Spiel nicht.", parent=self.win)
            return
        vices, seen = [], set()
        for v, lv in self.vice_vars:
            attr = GameData.id_of(v.get())
            if attr and attr not in seen:
                seen.add(attr)
                vices.append([attr, int(lv.get())])
        spec = {"conf": row["ID"], "level": max(0, min(int(self.plevel.get()), MAX_LEVEL[rare])),
                "main": GameData.id_of(self.main.get()), "vices": vices, "count": int(self.count.get())}
        self.pending_specs.append(spec)
        self.spec_list.insert("end", i18n.tr(self.describe(spec)))

    def add_full_set(self):
        suit = GameData.id_of(self.suit.get())
        rare = next((k for k, v in RARITY.items() if v == self.rare.get()), 4)
        for pos in POSITIONS:
            row = self.data.specialty.get((suit, rare, pos))
            if not row:
                continue
            level = MAX_LEVEL[rare]
            vices = [[a, 1 if i < level // 3 else 0] for i, a in enumerate(self.data.vices(row)[:4])]
            spec = {"conf": row["ID"], "level": level, "main": self.data.mains(row)[0], "vices": vices,
                    "count": 1}
            self.pending_specs.append(spec)
            self.spec_list.insert("end", i18n.tr(self.describe(spec)))

    def clear_specs(self):
        self.pending_specs.clear()
        self.spec_list.delete(0, "end")
        self.pending_power.clear()
        self.pending_reso.clear()
        self.reso_all.set("unverändert")
        self.pending_coaches.clear()
        for var in (self.unlock_all, self.unlock_cheers, self.unlock_cosmetics):
            var.set(False)
        if hasattr(self, "coach_list"):
            self.coach_list.delete(0, "end")

    def describe(self, s):
        d = self.data
        row = next((r for r in d.specialty.values() if r["ID"] == s["conf"]), {})
        vice = ", ".join(f"{d.attr.get(a, a)}+{lv}" for a, lv in s["vices"])
        return (f"{s['count']}x {d.suits.get(row.get('SuitID'), '?')} · Platz {(row.get('Position') or 0) + 1} · "
                f"Stufe {row.get('Rare')} · Lv {s['level']} · {d.attr.get(s['main'], s['main'])} | {vice}")

    # --- coaches: build your own (queue entry "coach", server/coach.py check_build) -----------

    def tab_coach(self, nb):
        f = self.frame(nb)
        d = self.data
        tk.Label(f, text="Eigenen Trainer bauen: Trainer und Stufe wählen, dann je Platz eine Position und einen Buff. "
                         "Wie im Spiel gibt es je einen Platz ab Stufe 3, 6, 9, 12 und 15. Der Trainer steht nach dem "
                         "nächsten Login in der Trainer-Liste.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9), wraplength=700, justify="left").pack(
            anchor="w", padx=12, pady=(10, 4))
        head = tk.Frame(f, bg=PANEL)
        head.pack(anchor="w", padx=12, pady=4)
        grid = tk.Frame(f, bg=PANEL)
        grid.pack(anchor="w", padx=12, pady=4)
        self.coach_var = tk.StringVar()
        self.coach_level = tk.IntVar(value=15)
        self.coach_lock = tk.BooleanVar(value=True)
        self.coach_all = tk.BooleanVar(value=False)
        names = [f"{r['name']} ({i})" for i, r in sorted(d.coaches.items(), key=lambda e: (-e[1]["CoachRare"], e[0]))]
        tk.Label(head, text="Trainer:", bg=PANEL, fg=FG).pack(side="left")
        ttk.Combobox(head, textvariable=self.coach_var, values=names, state="readonly", width=34).pack(
            side="left", padx=6)
        tk.Label(head, text="Stufe:", bg=PANEL, fg=FG).pack(side="left", padx=(12, 4))
        tk.Spinbox(head, from_=1, to=15, textvariable=self.coach_level, width=4).pack(side="left")
        self.coach_rows = []
        jobs = [""] + [f"{n} ({j})" for j, n in d.jobs.items()]
        for i, lv in enumerate(COACH_BUFF_LEVELS):
            tk.Label(grid, text=f"Platz {i + 1} (ab Stufe {lv}):", bg=PANEL, fg=FG).grid(
                row=1 + i, column=0, sticky="w", pady=2)
            job, buff = tk.StringVar(), tk.StringVar()
            ttk.Combobox(grid, textvariable=job, values=jobs, state="readonly", width=18).grid(
                row=1 + i, column=1, sticky="w")
            box = ttk.Combobox(grid, textvariable=buff, state="readonly", width=62)
            box.grid(row=1 + i, column=2, sticky="w", padx=(8, 0))
            buff.trace_add("write", lambda *_a, v=buff: self.coach_detail.set(v.get()))
            self.coach_rows.append((job, buff, box))
        self.coach_detail = tk.StringVar()  # the chosen buff's whole text (the boxes cut long ones off)
        tk.Label(f, textvariable=self.coach_detail, bg=PANEL, fg=GOLD, font=("Segoe UI", 9), wraplength=700,
                 justify="left", height=2, anchor="nw").pack(fill="x", padx=12)
        opts = tk.Frame(f, bg=PANEL)
        opts.pack(anchor="w", padx=12, pady=2)
        tk.Checkbutton(opts, text="Trainer sperren (kann nicht zerlegt werden)", variable=self.coach_lock, bg=PANEL,
                       fg=FG, selectcolor=BG, activebackground=PANEL).pack(side="left")
        tk.Checkbutton(opts, text="Auch schwächere Werte zeigen", variable=self.coach_all, bg=PANEL, fg=FG,
                       selectcolor=BG, activebackground=PANEL, command=self.refresh_coach).pack(side="left", padx=12)
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(anchor="w", padx=12, pady=6)
        tk.Button(btns, text="Hinzufügen", command=self.add_coach, bg="#0f3460", fg="white",
                  relief="flat").pack(side="left")
        tk.Button(btns, text="Liste leeren", command=self.clear_specs, bg="#53354a", fg="white",
                  relief="flat").pack(side="left", padx=6)
        self.coach_list = tk.Listbox(f, height=6, bg="#0f0f23", fg="#00ff88", font=("Consolas", 9))
        self.coach_list.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.coach_var.trace_add("write", lambda *_: self.refresh_coach())
        self.coach_var.set(names[0])
        return f

    def coach_buff_choices(self, conf, every=False):
        """The buffs this coach can have, strongest rarity first; without `every` the best roll of each kind."""
        d = self.data
        pool = [b for b in d.coaches.get(conf, {}).get("CoachBuffId") or [] if b in d.coach_buffs]
        if not every:
            pool = [b for b in pool if d.coach_buffs[b].get("BuffNumber") == d.coach_buffs[b].get("MaxNumber")]
        pool.sort(key=lambda b: (-d.coach_buffs[b].get("Rare", 0), b))
        return [f"★{d.coach_buffs[b].get('Rare')} {d.coach_buffs[b]['text']} ({b})" for b in pool]

    def refresh_coach(self):
        conf = GameData.id_of(self.coach_var.get())
        row = self.data.coaches.get(conf)
        if not row:
            return
        self.coach_level.set(min(int(self.coach_level.get() or 1), self.data.coach_top.get(row["CoachRare"], 15)))
        choices = [""] + self.coach_buff_choices(conf, self.coach_all.get())
        for _job, buff, box in self.coach_rows:
            box.configure(values=choices)
            if buff.get() not in choices:
                buff.set("")

    def check_coach(self, conf, level, buffs):
        """Same rules as the server (coach.check_build); returns an error text or None."""
        d = self.data
        row = d.coaches.get(conf)
        if not row:
            return "Bitte einen Trainer wählen."
        top = d.coach_top.get(row["CoachRare"], 15)
        if not 1 <= level <= top:
            return f"Stufe {level} geht nicht (1 bis {top})."
        slots = sum(1 for lv in COACH_BUFF_LEVELS if lv <= level)
        if len(buffs) > slots:
            return f"Zu viele Buffs: Stufe {level} erlaubt {slots}."
        if any(b not in (row.get("CoachBuffId") or []) for _j, b in buffs):
            return "Diesen Buff kann der Trainer nicht haben."
        return None

    def add_coach(self):
        conf = GameData.id_of(self.coach_var.get())
        try:
            level = int(self.coach_level.get())
        except (ValueError, tk.TclError):
            level = 0
        buffs = []
        for n, (job, buff, _box) in enumerate(self.coach_rows):
            j, b = GameData.id_of(job.get()), GameData.id_of(buff.get())
            if bool(j) != bool(b):
                messagebox.showerror("Admin Mode", f"Platz {n + 1}: bitte Position und Buff wählen (oder beides leer).",
                                     parent=self.win)
                return
            if j:
                buffs.append([j, b])
        err = self.check_coach(conf, level, buffs)
        if err:
            messagebox.showerror("Admin Mode", err, parent=self.win)
            return
        spec = {"conf": conf, "level": level, "buffs": buffs, "lock": bool(self.coach_lock.get())}
        self.pending_coaches.append(spec)
        self.coach_list.insert("end", i18n.tr(self.describe_coach(spec)))

    def describe_coach(self, s):
        d = self.data
        buffs = ", ".join(f"{d.jobs.get(j, j)}: {b}" for j, b in s["buffs"]) or "ohne Buffs"
        return f"{d.coaches[s['conf']]['name']} · Stufe {s['level']} · {buffs}"

    # --- apply ---------------------------------------------------------------------

    # --- power (match strength per card) ------------------------------------------------

    STAT_LABELS = [(0, "Schnellangriff"), (1, "Kraftangriff"), (2, "Zuspiel"), (3, "Block"),
                   (4, "Annahme"), (5, "Aufschlag"), (6, "Retten")]

    def load_cards(self):
        """[(label, inst, conf, level, star, train)] of the chosen account, best cards first."""
        server = str(self.base / "server")
        if server not in sys.path:
            sys.path.insert(0, server)
        import starter
        import wire
        gid = int(self.target_gid() or 0)
        base = starter.baseline_for(gid)
        cards = dict(base.cards)
        acc_dir = self.base / "server" / "accounts"
        path = acc_dir / (f"{gid}.json" if base is starter.CAPTURED else f"{gid}-starter.json")
        try:
            d = json.loads(path.read_text("utf-8"))
        except Exception:
            d = {}
        for h in d.get("cards", []):
            raw = bytes.fromhex(h)
            cards[wire.get(wire.parse(raw), 1)] = raw
        for inst in d.get("removed_cards", []):
            cards.pop(inst, None)
        self.saved_power = (d.get("admin_flags") or {}).get("power", {})
        out = []
        for inst, raw in cards.items():
            f = wire.parse(raw)
            conf, level, star, train = (wire.get(f, n, 0) for n in (3, 4, 6, 7))
            name = self.data.card_name(conf)
            out.append((f"{name}  Lv.{level} \u2605{star}  ({inst})", inst, conf, level, star, train))
        out.sort(key=lambda c: (-c[3], -c[4], c[0]))
        return out

    def base_stats(self, card):
        try:
            import matches
            attrs = matches.card_attrs(card[2], card[3], card[4], card[5])
            return {a.type: a.val // matches.SCALE for a in attrs}
        except Exception:
            return {}

    def tab_power(self, nb):
        f = self.frame(nb)
        tk.Label(f, text="Stärke im Match: Prozent für alle Werte und/oder einzelne Werte direkt setzen "
                         "(leer = unverändert). Gilt für Matches, nicht für die Anzeige im Kartenmenü.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9), wraplength=700, justify="left").pack(
            anchor="w", padx=12, pady=(10, 4))
        row = tk.Frame(f, bg=PANEL)
        row.pack(anchor="w", padx=12, pady=4)
        tk.Label(row, text="Karte:", bg=PANEL, fg=FG).pack(side="left")
        self.card_var = tk.StringVar()
        self.card_box = ttk.Combobox(row, textvariable=self.card_var, width=58)
        self.card_box.pack(side="left", padx=6)
        tk.Button(row, text="Laden", command=self.refresh_cards, bg="#0f3460", fg="white",
                  relief="flat").pack(side="left")
        self.card_var.trace_add("write", lambda *_: self.show_base())

        grid = tk.Frame(f, bg=PANEL)
        grid.pack(anchor="w", padx=12, pady=4)
        tk.Label(grid, text="Alle Werte in %:", bg=PANEL, fg=FG).grid(row=0, column=0, sticky="w")
        self.pct = tk.IntVar(value=100)
        tk.Spinbox(grid, from_=1, to=10000, increment=10, textvariable=self.pct, width=7).grid(
            row=0, column=1, sticky="w")
        self.stat_vars = {}
        self.base_labels = {}
        for i, (t, name) in enumerate(self.STAT_LABELS):
            tk.Label(grid, text=f"{name}:", bg=PANEL, fg=FG).grid(row=1 + i, column=0, sticky="w")
            v = tk.StringVar()
            tk.Entry(grid, textvariable=v, width=10).grid(row=1 + i, column=1, sticky="w", pady=1)
            lbl = tk.Label(grid, text="", bg=PANEL, fg="#aaaaaa")
            lbl.grid(row=1 + i, column=2, sticky="w", padx=8)
            self.stat_vars[t] = v
            self.base_labels[t] = lbl

        rr = tk.Frame(f, bg=PANEL)
        rr.pack(anchor="w", padx=12, pady=(4, 0))
        tk.Label(rr, text="Skill-Resonance:", bg=PANEL, fg=FG).pack(side="left")
        self.reso_card = TrVar(value=i18n.tr("unverändert"))
        ttk.Combobox(rr, textvariable=self.reso_card, values=[i18n.tr(k) for k in RESO_CHOICES],
                     state="readonly", width=14).pack(side="left", padx=6)
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(anchor="w", padx=12, pady=6)
        tk.Button(btns, text="Hinzufügen", command=self.add_power, bg="#0f3460", fg="white",
                  relief="flat").pack(side="left")
        tk.Button(btns, text="Karte zurücksetzen", command=self.reset_power, bg="#53354a", fg="white",
                  relief="flat").pack(side="left", padx=6)
        self.power_list = tk.Listbox(f, height=6, bg="#0f0f23", fg="#00ff88", font=("Consolas", 9))
        self.power_list.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.cards = []
        self.saved_power = {}
        return f

    def refresh_cards(self):
        try:
            self.cards = self.load_cards()
        except Exception as e:
            messagebox.showerror("Admin Mode", f"Karten konnten nicht geladen werden: {e}", parent=self.win)
            return
        self.card_box.configure(values=["Alle Karten (*)"] + [c[0] for c in self.cards])
        self.card_var.set("Alle Karten (*)")
        self.power_list.delete(0, "end")
        for key, m in self.saved_power.items():
            self.power_list.insert("end", i18n.tr("gespeichert: " + self.describe_power(key, m)))

    def selected_card(self):
        label = self.card_var.get()
        if label.startswith("Alle Karten"):
            return "*", None
        card = next((c for c in self.cards if c[0] == label), None)
        if card is None and label.strip():  # typed text: first card whose name contains it
            card = next((c for c in self.cards if label.strip().lower() in c[0].lower()), None)
        return (str(card[1]), card) if card else (None, None)

    def show_base(self):
        key, card = self.selected_card()
        stats = self.base_stats(card) if card else {}
        for t, lbl in self.base_labels.items():
            lbl.configure(text=f"normal: {stats[t]}" if t in stats else "")

    def describe_power(self, key, m):
        if m is None:
            return f"{self.card_label(key)}: zurücksetzen"
        names = dict(self.STAT_LABELS)
        stats = ", ".join(f"{names.get(int(t), t)}={v}" for t, v in m.get("stats", {}).items())
        return f"{self.card_label(key)}: {m.get('pct', 100)} %" + (f" | {stats}" if stats else "")

    def card_label(self, key):
        if key == "*":
            return "Alle Karten"
        card = next((c for c in self.cards if str(c[1]) == str(key)), None)
        return card[0] if card else str(key)

    def add_power(self):
        key, card = self.selected_card()
        if key is None:
            messagebox.showerror("Admin Mode", "Erst „Laden“ drücken und eine Karte wählen.", parent=self.win)
            return
        stats = {}
        for t, v in self.stat_vars.items():
            text = v.get().strip().replace(".", "")
            if text:
                if not text.isdigit():
                    messagebox.showerror("Admin Mode", f"Ungültiger Wert: {v.get()}", parent=self.win)
                    return
                stats[str(t)] = int(text)
        reso = self.reso_choice(self.reso_card.get())
        if reso is not None:
            self.pending_reso[key] = reso
            self.power_list.insert("end", f"{self.card_label(key)}: Skill-Resonance {self.reso_card.get()}")
            self.reso_card.set("unverändert")
        m = {"pct": max(1, int(self.pct.get())), "stats": stats}
        if reso is None or stats or m["pct"] != 100:
            self.pending_power[key] = m
            self.power_list.insert("end", i18n.tr(self.describe_power(key, m)))
        self.pct.set(100)  # fresh fields for the next card
        for v in self.stat_vars.values():
            v.set("")

    @staticmethod
    def reso_choice(text):
        """Value for the server ({"resonance": ...}) of a box entry, None = unchanged."""
        for k, v in RESO_CHOICES.items():
            if text in (k, i18n.tr(k)):
                return v
        return None

    def reset_power(self):
        key, _ = self.selected_card()
        if key is None:
            return
        self.pending_power[key] = None
        self.power_list.insert("end", i18n.tr(self.describe_power(key, None)))

    def collect(self):
        actions = []
        if self.level_on.get():
            actions.append({"level": int(self.level.get())})
        cur = {}
        for cid, (on, val) in self.cur.items():
            if on.get():
                try:
                    cur[str(cid)] = max(0, int(str(val.get()).replace(".", "").replace(" ", "")))
                except ValueError:
                    raise ValueError(f"Ungültiger Wert für Währung {cid}: {val.get()}")
        if cur:
            actions.append({"currency": cur})
        if self.stamina.get() != "keep":
            actions.append({"flags": {"infinite_stamina": self.stamina.get() == "on"}})
        if self.perfect.get() != "keep":
            actions.append({"flags": {"perfect_potentials": self.perfect.get() == "on"}})
        if self.unlock_all.get():
            actions.append({"unlock_all": True})
        elif self.unlock_cheers.get() or self.unlock_cosmetics.get():
            actions.append({"unlock": [k for k, v in (("cheers", self.unlock_cheers), ("cosmetics", self.unlock_cosmetics))
                                       if v.get()]})
        if self.all_cards.get():
            actions.append({"all_cards": True})
        if self.max_cards.get():
            actions.append({"max_cards": True})
        if self.items_on.get():
            actions.append({"items": int(self.items.get())})
        if self.pending_scan:
            actions.append({"scan": self.pending_scan})
        best = next((v for k, v in BEST_CHOICES.items() if i18n.tr(k) == self.best_teams.get()), None)
        if best == "off":
            actions.append({"best_teams": {"off": True}})
        elif best:
            actions.append({"best_teams": {"edition": best, "focus": self.best_focus.get().lower()}})
        if self.pending_power:
            actions.append({"power": dict(self.pending_power)})
        reso = dict(self.pending_reso)
        if self.reso_choice(self.reso_all.get()) is not None:
            reso.setdefault("*", self.reso_choice(self.reso_all.get()))
        if reso:
            actions.append({"resonance": reso})
        if self.pending_specs:
            actions.append({"specialties": list(self.pending_specs)})
        if self.pending_coaches:
            actions.append({"coach": list(self.pending_coaches)})
        return actions

    def apply(self):
        gid = self.gid.get()
        if not gid:
            messagebox.showerror("Admin Mode", "Kein freigeschaltetes Konto.", parent=self.win)
            return
        try:
            actions = self.collect()
        except ValueError as e:
            messagebox.showerror("Admin Mode", str(e), parent=self.win)
            return
        if not actions:
            messagebox.showinfo("Admin Mode", "Nichts ausgewählt.", parent=self.win)
            return
        queue = self.base / "server" / "accounts" / "admin_queue"
        queue.mkdir(parents=True, exist_ok=True)
        if gid == ALL:  # every own account applies each batch once (server/admin.py apply_pending)
            path = queue / "all.json"
            try:
                old = json.loads(path.read_text("utf-8"))
            except Exception:
                old = []
            nid = max([int(e.get("id", 0)) for e in old] + [int(time.time())]) + 1
            path.write_text(json.dumps(old + [{"id": nid, "actions": actions}], indent=1), encoding="utf-8")
            who = "alle eigenen Konten"
        else:
            path = queue / f"{gid}.json"
            try:
                old = json.loads(path.read_text("utf-8"))
            except Exception:
                old = []
            path.write_text(json.dumps(old + actions, indent=1), encoding="utf-8")
            who = f"Konto {gid}"
        self.clear_specs()
        self.status.set("Gespeichert – Spiel startet neu, Änderungen kommen beim Login an. "
                        "Im Spiel einmal auf START tippen.")
        self.l.log(f"Admin Mode: {len(actions)} Änderung(en) für {who} vorgemerkt.")
        self.l.restart_game_clicked()
        self.win.after(5000, self.watch_done, time.time())

    def watch_done(self, since):
        """Show the report once the account that logs in has applied the changes."""
        if not self.win.winfo_exists():
            return
        self.login_var.set(self.last_login_text())
        try:
            d = json.loads((self.base / "server" / "accounts" / "admin_done" / f"{self.target_gid()}.json")
                           .read_text("utf-8"))
            done = d["time"] >= since
        except Exception:
            done = False
        if done:
            self.status.set(self.last_report())
        elif time.time() - since < 600:
            self.win.after(3000, self.watch_done, since)

    # --- rights (shared server) -------------------------------------------------------------

    def tab_rights(self, nb):
        f = self.frame(nb)
        tk.Label(f, text="Gemeinsamer Server: nur der Besitzer und die hier angehakten Spieler haben "
                         "Admin-Rechte. Alle anderen spielen normal. Spieler mit Rechten können im Spiel "
                         "unter Gift Code Befehle eingeben, z. B. /gold 100000, /allcards, /power 200, "
                         "/gold 5000 @Name. Jedes Konto ist an den Garena-Login gebunden, der es zuerst "
                         "benutzt hat; ein abgelehnter Login (z. B. neues Handy) lässt sich hier freigeben.",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9), wraplength=700, justify="left").pack(
            anchor="w", padx=12, pady=(10, 4))
        self.owner_var = TrVar()
        tk.Label(f, textvariable=self.owner_var, bg=PANEL, fg=GOLD, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=12, pady=(4, 6))
        holder = tk.Frame(f, bg=PANEL)
        holder.pack(fill="both", expand=True, padx=12, pady=4)
        canvas = tk.Canvas(holder, bg=PANEL, highlightthickness=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        self.rights_frame = tk.Frame(canvas, bg=PANEL)
        self.rights_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.rights_frame, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        row = tk.Frame(f, bg=PANEL)
        row.pack(anchor="w", padx=12, pady=8)
        tk.Button(row, text="Rechte speichern", command=self.save_rights, bg="#0f3460", fg="white",
                  relief="flat").pack(side="left")
        tk.Button(row, text="Neu laden", command=self.fill_rights, bg="#53354a", fg="white",
                  relief="flat").pack(side="left", padx=6)
        self.right_vars = {}
        self.fill_rights()
        return f

    def fill_rights(self):
        for w in self.rights_frame.winfo_children():
            w.destroy()
        st = self.admins()
        owner = str(st.get("owner") or "")
        players = self.players()
        self.owner_var.set(f"Besitzer: {players.get(owner) or ''} ({owner})" if owner else
                           "Noch kein Besitzer: im Spiel das Admin-Passwort als Gift Code eingeben.")
        self.right_vars = {}
        binds = self.bindings()
        for gid in sorted(players, key=lambda g: (g != owner, "pending" not in binds.get(g, {}),
                                                  (players[g] or "~").lower())):
            row = tk.Frame(self.rights_frame, bg=PANEL)
            row.pack(fill="x", anchor="w", pady=1)
            label = f"{players[gid] or '(ohne Namen)'}  ({gid})"
            if gid == owner:
                tk.Label(row, text="\u2605 " + label + "  (Besitzer)", bg=PANEL, fg=GOLD, width=44,
                         anchor="w").pack(side="left")
            else:
                var = tk.BooleanVar(value=gid in st["admins"])
                tk.Checkbutton(row, text=label, variable=var, bg=PANEL, fg=FG, selectcolor=BG,
                               activebackground=PANEL, anchor="w", width=40).pack(side="left")
                self.right_vars[gid] = var
            b = binds.get(gid)
            if b and "pending" in b:
                when = time.strftime("%d.%m. %H:%M", time.localtime(b["pending"].get("time", 0)))
                tk.Label(row, text=f"Login abgelehnt ({when})", bg=PANEL, fg="#ff6b6b").pack(side="left", padx=6)
                tk.Button(row, text="Freigeben", command=lambda g=gid: self.approve_login(g), bg="#2d4a3e",
                          fg="white", relief="flat").pack(side="left", padx=2)
            elif b:
                tk.Label(row, text="gebunden", bg=PANEL, fg="#9be7a5").pack(side="left", padx=6)
            if b:
                tk.Button(row, text="Lösen", command=lambda g=gid: self.release_login(g), bg="#53354a",
                          fg="white", relief="flat").pack(side="left", padx=2)
        i18n.translate_tree(self.rights_frame)

    # token binding (server/bindings.py): an account belongs to the Garena login that used it first

    def bindings_file(self):
        return self.base / "server" / "accounts" / "bindings.json"

    def bindings(self):
        try:
            return json.loads(self.bindings_file().read_text("utf-8"))
        except Exception:
            return {}

    def save_bindings(self, data):
        f = self.bindings_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(f)

    def approve_login(self, gid):
        """Accept the refused login: its token becomes the bound one (as bindings.approve)."""
        data = self.bindings()
        entry = data.get(gid)
        if not entry or "pending" not in entry:
            return
        entry["hash"] = entry.pop("pending")["hash"]
        entry["last"] = int(time.time())
        self.save_bindings(data)
        self.status.set(f"Login von {gid} freigegeben – der Spieler kann sich jetzt anmelden.")
        self.fill_rights()

    def release_login(self, gid):
        """Forget the binding: the next login of this account binds anew (as bindings.release)."""
        if not messagebox.askyesno("Admin Mode", f"Bindung von {gid} lösen? Der nächste Login mit dieser "
                                                 "ID wird dann als neuer Besitzer des Kontos gespeichert.",
                                   parent=self.win):
            return
        data = self.bindings()
        if data.pop(gid, None) is not None:
            self.save_bindings(data)
        self.status.set(f"Bindung von {gid} gelöst.")
        self.fill_rights()

    def save_rights(self):
        st = self.admins()
        players = self.players()
        st["admins"] = {g: players.get(g, "") for g, v in self.right_vars.items() if v.get()}
        self.admins_file().parent.mkdir(parents=True, exist_ok=True)
        self.admins_file().write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        self.status.set(f"Rechte gespeichert: {len(st['admins'])} Admin(s) neben dem Besitzer.")
        self.l.log(f"Admin-Rechte gespeichert ({len(st['admins'])} Admins).")

    def lock(self):
        f = self.base / "server" / "accounts" / "admin_unlocked.json"
        try:
            state = json.loads(f.read_text("utf-8"))
            if self.shared():
                state.pop(self.gid.get(), None)
            else:  # own server: the unlock covers all accounts
                state = {}
            f.write_text(json.dumps(state, indent=1), encoding="utf-8")
        except Exception:
            pass
        if self.shared():  # a granted admin loses the rights (the owner stays owner)
            st = self.admins()
            if st["admins"].pop(self.gid.get(), None) is not None:
                self.admins_file().write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
        self.l.log("Admin Mode gesperrt (neu freischalten: Passwort im Spiel als Geschenkcode).")
        self.win.destroy()
