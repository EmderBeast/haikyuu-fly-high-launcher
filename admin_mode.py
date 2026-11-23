"""
Admin Mode window of the launcher (admin build only).

Writes the wanted changes to server/accounts/admin_queue/<gid>.json and restarts
the game; the server applies them at the next login (server/admin.py) and reports
what it did in server/accounts/admin_done/<gid>.json.
"""
import json
import re
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

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


class GameData:
    """Names and potential tables from gamedata/cfg (German texts from language.json)."""

    def __init__(self, base: Path):
        cfg = base / "gamedata" / "cfg"
        load = lambda n: json.loads((cfg / f"{n}.json").read_text(encoding="utf-8"))
        lang = {r["ID"]: r for r in load("language")}

        def de(ref):
            m = re.findall(r"\d+", str(ref))
            row = lang.get(int(m[0])) if m else None
            return (row.get("German") or row.get("English")) if row else None

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
        self.specialty = {}  # (suit, rare, position) -> row
        for r in load("specialty"):
            self.specialty[(r["SuitID"], r["Rare"], r.get("Position") or 0)] = r

    def attr_label(self, attr_id):
        return f"{self.attr.get(attr_id, attr_id)} ({attr_id})"

    @staticmethod
    def id_of(label):
        m = re.search(r"\((\d+)\)\s*$", label or "")
        return int(m.group(1)) if m else None

    def mains(self, row):
        m = row["MainattrID"]
        return m if isinstance(m, list) else [m]

    def vices(self, row):
        return self.vice_pools.get(row.get("ViceattrPool"), [])


class AdminWindow:
    def __init__(self, launcher):
        self.l = launcher
        self.base = launcher.base
        self.data = GameData(self.base)
        self.pending_specs = []

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
        self.gid = tk.StringVar()
        gids = sorted(self.unlocked())
        self.gid_box = ttk.Combobox(top, textvariable=self.gid, values=gids, state="readonly", width=14)
        self.gid_box.pack(side="left")
        if gids:
            self.gid.set(gids[-1])

        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=14, pady=6)
        nb.add(self.tab_account(nb), text="Konto")
        nb.add(self.tab_cards(nb), text="Charaktere & Gegenstände")
        nb.add(self.tab_potentials(nb), text="Potentials")

        bottom = tk.Frame(self.win, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(bottom, text="Übernehmen & Spiel neu starten", command=self.apply, bg="#0f3460",
                  fg="white", font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2",
                  activebackground="#1a5276", activeforeground="white").pack(side="left")
        tk.Button(bottom, text="Admin Mode sperren", command=self.lock, bg="#53354a", fg="white",
                  font=("Segoe UI", 10), relief="flat", cursor="hand2").pack(side="right")
        self.status = tk.StringVar(value=self.last_report())
        tk.Label(self.win, textvariable=self.status, bg=BG, fg="#9be7a5", font=("Segoe UI", 9),
                 wraplength=720, justify="left").pack(fill="x", padx=14, pady=(0, 10))

    # --- files --------------------------------------------------------------------

    def unlocked(self):
        try:
            return json.loads((self.base / "server" / "accounts" / "admin_unlocked.json").read_text("utf-8"))
        except Exception:
            return {}

    def last_report(self):
        try:
            d = json.loads((self.base / "server" / "accounts" / "admin_done" / f"{self.gid.get()}.json")
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
        for var, text in ((self.all_cards, "Alle Charaktere freischalten (je eine Karte von jedem, den du noch nicht hast)"),
                          (self.max_cards, "Alle Karten auf 6 Sterne und Level 80")):
            tk.Checkbutton(f, text=text, variable=var, bg=PANEL, fg=FG, selectcolor=BG,
                           activebackground=PANEL, wraplength=680, justify="left").pack(anchor="w", padx=12, pady=(14, 2))
        r = tk.Frame(f, bg=PANEL)
        r.pack(anchor="w", padx=12, pady=(14, 2))
        tk.Checkbutton(r, text="Alle Gegenstände auf Anzahl:", variable=self.items_on, bg=PANEL, fg=FG,
                       selectcolor=BG, activebackground=PANEL).pack(side="left")
        tk.Spinbox(r, from_=0, to=99999, textvariable=self.items, width=7).pack(side="left", padx=6)
        tk.Label(f, text="Alle Sorten aus dem normalen Inventar (Tickets, Material, Geschenke …).",
                 bg=PANEL, fg="#aaaaaa", font=("Segoe UI", 9)).pack(anchor="w", padx=36)
        return f

    def tab_potentials(self, nb):
        f = self.frame(nb)
        d = self.data
        grid = tk.Frame(f, bg=PANEL)
        grid.pack(anchor="w", padx=12, pady=(12, 4))

        def label(r, text):
            tk.Label(grid, text=text, bg=PANEL, fg=FG).grid(row=r, column=0, sticky="w", pady=2)

        self.suit = tk.StringVar()
        self.rare = tk.StringVar(value=RARITY[4])
        self.pos = tk.StringVar(value="Platz 1")
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
        ttk.Combobox(grid, textvariable=self.pos, values=[f"Platz {p + 1}" for p in POSITIONS],
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
        self.spec_list.insert("end", self.describe(spec))

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
            self.spec_list.insert("end", self.describe(spec))

    def clear_specs(self):
        self.pending_specs.clear()
        self.spec_list.delete(0, "end")

    def describe(self, s):
        d = self.data
        row = next((r for r in d.specialty.values() if r["ID"] == s["conf"]), {})
        vice = ", ".join(f"{d.attr.get(a, a)}+{lv}" for a, lv in s["vices"])
        return (f"{s['count']}x {d.suits.get(row.get('SuitID'), '?')} · Platz {(row.get('Position') or 0) + 1} · "
                f"Stufe {row.get('Rare')} · Lv {s['level']} · {d.attr.get(s['main'], s['main'])} | {vice}")

    # --- apply ---------------------------------------------------------------------

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
        if self.all_cards.get():
            actions.append({"all_cards": True})
        if self.max_cards.get():
            actions.append({"max_cards": True})
        if self.items_on.get():
            actions.append({"items": int(self.items.get())})
        if self.pending_specs:
            actions.append({"specialties": list(self.pending_specs)})
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
        path = queue / f"{gid}.json"
        try:
            old = json.loads(path.read_text("utf-8"))
        except Exception:
            old = []
        path.write_text(json.dumps(old + actions, indent=1), encoding="utf-8")
        self.clear_specs()
        self.status.set("Gespeichert – Spiel startet neu, Änderungen kommen beim Login an. "
                        "Im Spiel einmal auf START tippen.")
        self.l.log(f"Admin Mode: {len(actions)} Änderung(en) für Konto {gid} vorgemerkt.")
        self.l.restart_game_clicked()
        self.win.after(5000, self.watch_done, path)

    def watch_done(self, path):
        if not self.win.winfo_exists():
            return
        if path.exists():
            self.win.after(3000, self.watch_done, path)
        else:
            self.status.set(self.last_report())

    def lock(self):
        f = self.base / "server" / "accounts" / "admin_unlocked.json"
        try:
            state = json.loads(f.read_text("utf-8"))
            state.pop(self.gid.get(), None)
            f.write_text(json.dumps(state, indent=1), encoding="utf-8")
        except Exception:
            pass
        self.l.log("Admin Mode gesperrt (neu freischalten: Passwort im Spiel als Geschenkcode).")
        self.win.destroy()
