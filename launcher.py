"""
Haikyuu Fly High - Private Server Launcher

One click: starts the game server and the mitmproxy pass-through, starts
LDPlayer if needed, points the emulator at the private server (or back at
Garena) and starts the game.

Runs as a plain script (python launcher.py) or as the PyInstaller build
HFH-Launcher.exe. The same program also acts as the server / proxy child
process (--run-server / --run-mitm / --run-relay), so the .exe needs no installed Python.

Server address (settings "server", field in the window): empty = the local server from the
package; "host[:port]" = a shared multiplayer server. Then the launcher runs a relay
(server/relay.py) on 127.0.0.1:10104 instead of the local server; the emulator side stays the
same. A package can preset it in launcher_defaults.json ({"server": "..."}, build option --server).

Data folder (the folder with server/, decoded/, gamedata/, patch/):
  --base <dir>, else the exe's / script's folder or one of its parents.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import launcher_i18n as i18n

IN_GAME_PORT = 10104  # the port the game connects to (redirected inside the emulator)
GAME_PORT = int(os.environ.get("HFH_PORT") or IN_GAME_PORT)  # our server; tests: HFH_PORT=10105
PROXY_PORT = 8082
GAME_PACKAGE = "com.garena.game.haikyu"
GAME_ACTIVITY = f"{GAME_PACKAGE}/.icon_2_Activity"
IPTABLES_RULE = (f"OUTPUT -p tcp --dport {IN_GAME_PORT} "
                 f"-j DNAT --to-destination 127.0.0.1:{GAME_PORT}")

APP_DIR = Path(os.environ.get("HFH_LAUNCHER_DIR") or
               Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HFH-Launcher")  # tests: own folder
SETTINGS_FILE = APP_DIR / "settings.json"
FROZEN = getattr(sys, "frozen", False)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LDPLAYER_PAGE = "https://www.ldplayer.net/"
UPDATE_REPO = "EmderBeast/haikyuu-fly-high-launcher"  # public repo with the release "launcher-latest"
UPDATE_PAGE = f"https://github.com/{UPDATE_REPO}/releases/latest"
UPDATE_ZIP = f"https://github.com/{UPDATE_REPO}/releases/download/launcher-latest/{{name}}.zip"
BACKUP_KEEP = 20
LOG_MAX = 1_000_000  # launcher.log is rotated to launcher.log.1 above this size  # automatic + manual backups of server/accounts kept in backups/

MODES = {"private": "Private Server", "garena": "Garena (offiziell)"}


# ----------------------------------------------------------------------------
# child process modes
# ----------------------------------------------------------------------------

def run_server(base: Path):
    server_dir = base / "server"
    os.chdir(server_dir)
    sys.path.insert(0, str(server_dir))
    import runpy
    runpy.run_path(str(server_dir / "server.py"), run_name="__main__")


def run_mitm(args):
    from mitmproxy.tools.main import mitmdump
    sys.argv = ["mitmdump"] + args
    sys.exit(mitmdump(args))


def run_relay(base: Path, args: list):
    """args: host:port [--listen PORT] (see server/relay.py)."""
    sys.path.insert(0, str(base / "server"))
    import relay
    relay.main(args)


def child_cmd(*args):
    """Command line that re-runs this program in a child mode."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def find_base(argv) -> Path:
    if "--base" in argv:
        return Path(argv[argv.index("--base") + 1]).resolve()
    start = Path(sys.executable if FROZEN else __file__).resolve().parent
    for d in [start, *start.parents]:
        if (d / "server" / "server.py").exists():
            return d
    return start


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_server(text: str) -> tuple[str, int] | None:
    """'host[:port]' -> (host, port); None for an empty address (= local server)."""
    text = (text or "").strip()
    if not text:
        return None
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit():
        host, port = text, str(GAME_PORT)
    if not host or " " in host or not 0 < int(port) < 65536:
        raise ValueError(f"Ungültige Server-Adresse: {text}")
    return host, int(port)


def reachable(host: str, port: int, timeout=5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def save_settings(data: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def run(cmd, timeout=15):
    """Run a command, return (ok, output)."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def ldplayer_installs():
    """Installed LDPlayer folders (newest first) that contain ldconsole.exe."""
    found = []
    try:
        import winreg
        for name in ("LDPlayer14", "LDPlayer9", "LDPlayer64", "LDPlayer4", "LDPlayer"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\XuanZhi\{name}") as k:
                    found.append(Path(winreg.QueryValueEx(k, "InstallDir")[0]))
            except OSError:
                pass
    except ImportError:
        pass
    for drive in "CDE":
        for name in ("LDPlayer14", "LDPlayer9"):
            found.append(Path(f"{drive}:/LDPlayer/{name}"))
    seen, result = set(), []
    for p in found:
        key = str(p).lower().rstrip("\\/")
        if key not in seen and (p / "ldconsole.exe").exists():
            seen.add(key)
            result.append(p)
    return result


class LDPlayer:
    def __init__(self, folder: Path):
        self.folder = folder
        self.console = str(folder / "ldconsole.exe")
        self.adb_exe = str(folder / "adb.exe")

    def instances(self):
        """[(index, name, running)] from `ldconsole list2`."""
        ok, out = run([self.console, "list2"])
        result = []
        for line in out.splitlines():
            parts = line.split(",")
            if len(parts) >= 5 and parts[0].isdigit():
                result.append((int(parts[0]), parts[1], parts[4] == "1"))
        return result

    def is_running(self, index):
        ok, out = run([self.console, "isrunning", "--index", str(index)])
        return out.strip() == "running"

    def launch(self, index):
        return run([self.console, "launch", "--index", str(index)])

    def quit(self, index):
        return run([self.console, "quit", "--index", str(index)])

    def enable_root(self, index):
        return run([self.console, "modify", "--index", str(index), "--root", "1"])


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

BG = "#1a1a2e"
PANEL = "#16213e"
ACCENT = "#e94560"
OK = "#00ff88"
WARN = "#ffaa00"
BAD = "#ff6b6b"
GREY = "#aaaaaa"


class Launcher:
    def __init__(self, base: Path):
        import tkinter as tk
        from tkinter import ttk, scrolledtext
        self.tk = tk
        self.base = base
        self.settings = load_settings()
        self.settings.setdefault("mode", "private")
        if "server" not in self.settings:  # a package for a shared server presets the address
            try:
                defaults = json.loads((base / "launcher_defaults.json").read_text(encoding="utf-8"))
                self.settings["server"] = str(defaults.get("server", ""))
            except Exception:
                self.settings["server"] = ""
        save_settings(self.settings)  # the proxy reads the mode from here

        i18n.LANG = i18n.detect(self.settings)
        self.installs = ldplayer_installs()
        self.ld = None
        self.serial = None
        self.server_proc = None
        self.mitm_proc = None
        self.busy = threading.Lock()
        self.closing = False

        self.root = tk.Tk()
        self.root.title("Haikyuu Fly High - Launcher")
        self.root.geometry("720x600")
        self.root.minsize(640, 520)
        self.root.configure(bg=BG)
        # game icon (made by build_launcher.py from the APK; bundled into the exe)
        assets = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent / "build"
        self.logo = None
        try:
            if (assets / "launcher.ico").exists():
                self.root.iconbitmap(default=str(assets / "launcher.ico"))
            if (assets / "launcher.png").exists():
                self.logo = tk.PhotoImage(file=str(assets / "launcher.png"))
        except Exception:
            pass

        font = ("Segoe UI", 10)
        header = tk.Frame(self.root, bg=BG)
        header.pack(pady=(12, 0))
        if self.logo:
            tk.Label(header, image=self.logo, bg=BG).pack(side="left", padx=(0, 12))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="Haikyuu Fly High", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(titles, text="Launcher", bg=BG, fg="#eeeeee", font=font).pack(anchor="w")
        tk.Button(header, text="DE / EN", command=self.toggle_language, bg=PANEL, fg="#eeeeee",
                  relief="flat", cursor="hand2", font=("Segoe UI", 8)).pack(side="left", padx=(16, 0))

        # mode panel
        mode_frame = tk.Frame(self.root, bg=PANEL, highlightbackground=ACCENT,
                              highlightthickness=1)
        mode_frame.pack(padx=20, pady=10, fill="x")
        self.mode_var = tk.StringVar()
        self.mode_label = tk.Label(mode_frame, textvariable=self.mode_var, bg=PANEL,
                                   fg=OK, font=("Segoe UI", 13, "bold"))
        self.mode_label.pack(pady=(8, 4))
        row = tk.Frame(mode_frame, bg=PANEL)
        row.pack(pady=(0, 10))
        btn = dict(fg="white", font=("Segoe UI", 11, "bold"), width=18, relief="flat",
                   cursor="hand2", activeforeground="white")
        self.private_btn = tk.Button(row, text="Private Server", bg="#0f3460",
                                     activebackground="#1a5276",
                                     command=lambda: self.switch_mode("private"), **btn)
        self.private_btn.pack(side="left", padx=6)
        self.garena_btn = tk.Button(row, text="Garena (offiziell)", bg="#53354a",
                                    activebackground="#6b3a5a",
                                    command=lambda: self.switch_mode("garena"), **btn)
        self.garena_btn.pack(side="left", padx=6)
        self.restart_btn = tk.Button(row, text="Spiel neu starten", bg="#2d4a3e",
                                     activebackground="#3d6a55",
                                     command=self.restart_game_clicked, **btn)
        self.restart_btn.pack(side="left", padx=6)
        # Admin Mode: only in the admin build (server/admin.json) and only after the
        # admin password was entered in game (Settings -> gift code)
        self.admin_available = (base / "server" / "admin.json").exists() and not self.remote()
        self.admin_btn = tk.Button(mode_frame, text="Admin Mode", bg="#7d5a00",
                                   activebackground="#a07400", command=self.open_admin, **btn)
        self.admin_window = None

        # emulator choice
        emu_row = tk.Frame(self.root, bg=BG)
        emu_row.pack(padx=20, fill="x")
        tk.Label(emu_row, text="Emulator:", bg=BG, fg="#eeeeee", font=font).pack(side="left")
        self.emu_choice = tk.StringVar()
        self.emu_box = ttk.Combobox(emu_row, textvariable=self.emu_choice, state="readonly",
                                    width=40)
        self.emu_box.pack(side="left", padx=8)
        self.emu_box.bind("<<ComboboxSelected>>", self.emu_selected)
        tk.Button(emu_row, text="Verbinden", command=self.connect_clicked, bg="#0f3460",
                  fg="white", relief="flat", cursor="hand2", font=font,
                  activebackground="#1a5276", activeforeground="white").pack(side="left")
        tk.Button(emu_row, text="Backup", command=self.backup_clicked, bg="#2d4a3e",
                  fg="white", relief="flat", cursor="hand2", font=font,
                  activebackground="#3d6a55", activeforeground="white").pack(side="left", padx=(8, 0))
        tk.Button(emu_row, text="Wiederherstellen", command=self.restore_clicked, bg="#53354a",
                  fg="white", relief="flat", cursor="hand2", font=font,
                  activebackground="#6b3a5a", activeforeground="white").pack(side="left", padx=(6, 0))
        self.ldplayer_btn = tk.Button(emu_row, text="LDPlayer laden", bg="#c97a00", fg="white",
                                      relief="flat", cursor="hand2", font=font,
                                      command=lambda: __import__("webbrowser").open(LDPLAYER_PAGE))
        self.update_btn = tk.Button(mode_frame, text="Update verfügbar – hier laden", command=self.open_update,
                                    bg="#c97a00", fg="white", relief="flat", cursor="hand2", font=font,
                                    activebackground="#e08a00", activeforeground="white")

        # server address: empty = local server, otherwise a shared multiplayer server
        srv_row = tk.Frame(self.root, bg=BG)
        srv_row.pack(padx=20, pady=(8, 0), fill="x")
        tk.Label(srv_row, text="Server:", bg=BG, fg="#eeeeee", font=font).pack(side="left")
        self.server_var = tk.StringVar(value=self.settings.get("server", ""))
        entry = tk.Entry(srv_row, textvariable=self.server_var, width=34, font=font,
                         bg="#0f0f23", fg="white", insertbackground="white", relief="flat")
        entry.pack(side="left", padx=8, ipady=3)
        entry.bind("<Return>", lambda _e: self.server_clicked())
        tk.Button(srv_row, text="Übernehmen", command=self.server_clicked, bg="#0f3460",
                  fg="white", relief="flat", cursor="hand2", font=font,
                  activebackground="#1a5276", activeforeground="white").pack(side="left")
        tk.Label(srv_row, text="leer = lokal, sonst host[:port]", bg=BG, fg=GREY,
                 font=("Segoe UI", 9)).pack(side="left", padx=8)

        # status line
        status = tk.Frame(self.root, bg=BG)
        status.pack(padx=20, pady=8, fill="x")
        self.status = {}
        for key, text in (("server", "Server"), ("proxy", "Proxy"),
                          ("emu", "Emulator"), ("game", "Spiel")):
            var = tk.StringVar(value=f"{text}: --")
            lbl = tk.Label(status, textvariable=var, bg=BG, fg=GREY, font=font)
            lbl.pack(side="left", padx=(0, 18))
            self.status[key] = (var, lbl, text)

        tk.Label(self.root, text="Log:", bg=BG, fg=GREY, font=("Segoe UI", 9)).pack(
            anchor="w", padx=20)
        self.log_box = scrolledtext.ScrolledText(self.root, height=14, bg="#0f0f23",
                                                 fg=OK, font=("Consolas", 9),
                                                 wrap="word", state="disabled")
        self.log_box.pack(padx=20, pady=(2, 12), fill="both", expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show_mode()
        self.fill_emulators()
        self.translate_widgets()
        if i18n.LANG != "de":
            i18n.patch_messagebox()
        self.poll_admin()

    # --- UI helpers (thread-safe) -------------------------------------------

    def translate_widgets(self, widget=None):
        """English UI: translate the text of every button/label once after building the window."""
        if i18n.LANG == "de":
            return
        widget = widget or self.root
        for child in widget.winfo_children():
            try:
                text = child.cget("text")
                if text:
                    child.configure(text=i18n.tr(text))
            except Exception:
                pass
            self.translate_widgets(child)

    def toggle_language(self):
        new = "en" if i18n.LANG == "de" else "de"
        self.settings["lang"] = new
        save_settings(self.settings)
        self.log("Sprache: Englisch – wird beim nächsten Start übernommen." if new == "en"
                 else "Sprache: Deutsch – wird beim nächsten Start übernommen.")

    def ui(self, fn, *args):
        if not self.closing:
            self.root.after(0, fn, *args)

    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + i18n.tr(msg)

        def add():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.ui(add)
        self.log_to_file(line)

    def log_to_file(self, line: str):
        """launcher.log next to the data folder (to send along when something goes wrong)."""
        try:
            f = self.base / "launcher.log"
            if f.exists() and f.stat().st_size > LOG_MAX:
                f.replace(f.with_suffix(".log.1"))
            with open(f, "a", encoding="utf-8") as out:
                out.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except OSError:
            pass

    def set_status(self, key, text, color):
        var, lbl, name = self.status[key]
        text, name = i18n.tr(text), i18n.tr(name)

        def apply():
            var.set(f"{name}: {text}")
            lbl.configure(fg=color)
        self.ui(apply)

    def show_mode(self, active=None):
        """active: mode actually set in the emulator (None = unknown)."""
        wanted = self.settings["mode"]
        text = f"Aktiver Modus: {MODES[wanted]}"
        remote = self.remote()
        if wanted == "private" and remote:
            text += f" – online: {remote[0]}"
        color = OK if wanted == "private" else "#5dade2"
        if active is not None and active != wanted:
            text += f"  (Emulator noch: {MODES[active]})"
            color = WARN

        def apply():
            self.mode_var.set(i18n.tr(text))
            self.mode_label.configure(fg=color)
            self.private_btn.configure(relief="sunken" if wanted == "private" else "flat")
            self.garena_btn.configure(relief="sunken" if wanted == "garena" else "flat")
        self.ui(apply)

    def fill_emulators(self):
        self.emu_entries = []
        for folder in self.installs:
            ld = LDPlayer(folder)
            for index, name, running in ld.instances():
                label = f"{folder.name} – {name} (#{index})" + (" – " + i18n.tr("läuft") if running else "")
                self.emu_entries.append((label, folder, index, running))
        labels = [e[0] for e in self.emu_entries]
        self.emu_box.configure(values=labels)
        if not self.emu_entries:
            self.emu_choice.set("Kein LDPlayer gefunden")
            return
        # running instance first, then the one used last time
        saved = (self.settings.get("ldplayer"), self.settings.get("index"))
        pick = next((e for e in self.emu_entries if e[3]), None) or next(
            (e for e in self.emu_entries if (str(e[1]), e[2]) == saved), None) or self.emu_entries[0]
        self.emu_choice.set(pick[0])

    def selected_emulator(self):
        for label, folder, index, running in self.emu_entries:
            if label == self.emu_choice.get():
                return folder, index
        return None, None

    def emu_selected(self, _event=None):
        folder, index = self.selected_emulator()
        if folder is not None:
            self.settings.update(ldplayer=str(folder), index=index)
            save_settings(self.settings)

    # --- background jobs -------------------------------------------------------

    def job(self, fn, *args):
        def wrapper():
            if not self.busy.acquire(blocking=False):
                self.log("Bitte warten, es läuft noch ein Vorgang ...")
                return
            try:
                fn(*args)
            except Exception as e:
                import traceback
                self.log(f"FEHLER: {e}")
                self.log(traceback.format_exc())
            finally:
                self.busy.release()
        threading.Thread(target=wrapper, daemon=True).start()

    def connect_clicked(self):
        self.emu_selected()
        self.job(self.full_start)

    def poll_admin(self):
        """Show the Admin Mode button once an account unlocked it in game."""
        if self.closing:
            return
        if self.admin_available and admin_unlocked(self.base):  # local server only
            if not self.admin_btn.winfo_ismapped():
                self.admin_btn.pack(pady=(0, 10))
                self.log("Admin Mode freigeschaltet.")
        elif self.admin_btn.winfo_ismapped():
            self.admin_btn.pack_forget()
        self.root.after(2000, self.poll_admin)

    def open_admin(self):
        if self.admin_window and self.admin_window.win.winfo_exists():
            self.admin_window.win.lift()
            return
        from admin_mode import AdminWindow
        self.admin_window = AdminWindow(self)

    def remote(self):
        """(host, port) of the shared server, None = local server."""
        try:
            return parse_server(self.settings.get("server", ""))
        except ValueError:
            return None

    def server_clicked(self):
        text = self.server_var.get().strip()
        try:
            parse_server(text)
        except ValueError as e:
            self.log(str(e))
            return
        if text == self.settings.get("server", ""):
            return
        self.settings["server"] = text
        save_settings(self.settings)
        self.log(f"Server: {text} (Multiplayer)" if text else "Server: lokal")
        self.show_mode()
        # Admin Mode is for the local server only (a multiplayer server disables it anyway)
        self.admin_available = (self.base / "server" / "admin.json").exists() and not text
        self.job(self.switch_server_job)

    def switch_server_job(self):
        """Replace our server/relay process and restart the game so it connects anew."""
        self.stop_proc(self.server_proc)
        self.server_proc = None
        for _ in range(20):  # wait until our process released the port
            if not port_in_use(GAME_PORT):
                break
            time.sleep(0.25)
        self.start_processes()
        if self.ld is not None and self.serial is not None and self.game_running():
            self.start_game(restart=True)

    @staticmethod
    def stop_proc(proc):
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    def restart_game_clicked(self):
        self.job(self.restart_game_job)

    def switch_mode(self, mode):
        self.settings["mode"] = mode
        save_settings(self.settings)
        self.show_mode()
        self.log(f"Modus gewechselt: {MODES[mode]}")
        self.job(self.apply_mode_job)

    # --- processes ---------------------------------------------------------------

    def start_processes(self):
        """Start server and proxy unless already running (ours or someone else's)."""
        if self.server_proc and self.server_proc.poll() is None and \
                self.mitm_proc and self.mitm_proc.poll() is None:
            return
        env = dict(os.environ, HFH_SETTINGS=str(SETTINGS_FILE), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        remote = self.remote()
        if self.server_proc and self.server_proc.poll() is None:
            pass  # ours (server or relay) is still running
        elif port_in_use(GAME_PORT):
            self.log(f"Port {GAME_PORT} ist schon belegt – nutze den laufenden Server."
                     + (" Für den Online-Server zuerst den anderen Server beenden." if remote else ""))
            self.set_status("server", "läuft (extern)", WARN if remote else OK)
        elif remote:
            host, port = remote
            if reachable(host, port):
                self.log(f"Online-Server {host}:{port} erreichbar.")
            else:
                self.log(f"Online-Server {host}:{port} ist nicht erreichbar (läuft er? Port offen?).")
            self.server_proc = subprocess.Popen(
                child_cmd("--run-relay", f"{host}:{port}", "--base", str(self.base)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                creationflags=NO_WINDOW)
            threading.Thread(target=self.stream, args=(self.server_proc, "Relay"),
                             daemon=True).start()
            if self.wait_port(GAME_PORT, self.server_proc, 15):
                self.set_status("server", f"online: {host}", OK)
            else:
                self.set_status("server", "Relay-Fehler", BAD)
                self.log("Relay ist nicht gestartet (siehe Log).")
        elif not (self.base / "server" / "server.py").exists():
            self.log(f"server/server.py nicht gefunden in {self.base}")
            self.set_status("server", "fehlt", BAD)
        else:
            self.server_proc = subprocess.Popen(
                child_cmd("--run-server", "--base", str(self.base)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                creationflags=NO_WINDOW)
            threading.Thread(target=self.stream, args=(self.server_proc, "Server"),
                             daemon=True).start()
            if self.wait_port(GAME_PORT, self.server_proc, 60):
                self.set_status("server", f"läuft (Port {GAME_PORT})", OK)
                self.log("Game-Server gestartet.")
            else:
                self.set_status("server", "Fehler", BAD)
                self.log("Game-Server ist nicht gestartet (siehe Log).")

        if port_in_use(PROXY_PORT):
            self.log(f"Port {PROXY_PORT} ist schon belegt – nutze den laufenden Proxy.")
            self.set_status("proxy", "läuft (extern)", OK)
        else:
            script = self.base / "patch" / "mitm_redirect.py"
            self.mitm_proc = subprocess.Popen(
                child_cmd("--run-mitm", "-s", str(script), "--mode", f"regular@{PROXY_PORT}",
                          "--set", "ssl_insecure=true", "--set", "termlog_verbosity=warn"),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                creationflags=NO_WINDOW)
            threading.Thread(target=self.stream, args=(self.mitm_proc, "Proxy"),
                             daemon=True).start()
            if self.wait_port(PROXY_PORT, self.mitm_proc, 60):
                self.set_status("proxy", f"läuft (Port {PROXY_PORT})", OK)
                self.log("Proxy gestartet.")
            else:
                self.set_status("proxy", "Fehler", BAD)
                self.log("Proxy ist nicht gestartet (siehe Log).")

    @staticmethod
    def wait_port(port, proc, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if port_in_use(port):
                return True
            if proc.poll() is not None:
                return False
            time.sleep(0.3)
        return False

    def stream(self, proc, name):
        try:
            for line in iter(proc.stdout.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self.log(f"[{name}] {text}")
        except Exception:
            pass

    # --- emulator ----------------------------------------------------------------

    def adb(self, *args, timeout=15):
        return run([self.ld.adb_exe, "-s", self.serial, *args], timeout=timeout)

    def su(self, script, timeout=20):
        return self.adb("shell", f"su -c '{script}'", timeout=timeout)

    def ensure_emulator(self):
        folder, index = self.selected_emulator()
        if folder is None:
            self.log("Kein LDPlayer gefunden. Bitte LDPlayer 9 oder 14 installieren "
                     "(Knopf „LDPlayer laden“), Haikyuu Fly High darin installieren, dann „Verbinden“.")
            self.set_status("emu", "nicht gefunden", BAD)
            self.ui(lambda: self.ldplayer_btn.pack(side="left", padx=(6, 0)))
            return False
        self.ld = LDPlayer(folder)
        self.serial = f"emulator-{5554 + 2 * index}"
        self.settings.update(ldplayer=str(folder), index=index)
        save_settings(self.settings)

        if not self.ld.is_running(index):
            self.log(f"Starte LDPlayer ({folder.name}, Instanz #{index}) ...")
            self.set_status("emu", "startet ...", WARN)
            self.ld.enable_root(index)  # only possible while stopped; needed for iptables
            self.ld.launch(index)
        else:
            self.set_status("emu", "verbinde ...", WARN)

        if not self.wait_boot(index):
            return False
        self.log(f"Emulator verbunden ({self.serial}).")

        ok, out = self.su("id")
        if "uid=0" not in out:
            if self.ask_main("LDPlayer hat keine Root-Berechtigung, die der Launcher für die Umleitung "
                             "braucht.\n\nSoll der Launcher LDPlayer kurz beenden, Root einschalten und "
                             "neu starten?"):
                self.log("Schalte Root ein und starte LDPlayer neu ...")
                self.set_status("emu", "Neustart (Root) ...", WARN)
                self.ld.quit(index)
                for _ in range(30):
                    if not self.ld.is_running(index):
                        break
                    time.sleep(1)
                self.ld.enable_root(index)
                self.ld.launch(index)
                if not self.wait_boot(index):
                    return False
                ok, out = self.su("id")
            if "uid=0" not in out:
                self.log("Kein Root im Emulator. In LDPlayer: Einstellungen → Sonstiges → "
                         "Root-Berechtigung: An, dann LDPlayer neu starten.")
                self.set_status("emu", "kein Root", BAD)
                return False
            self.log("Root ist jetzt an.")

        ok, out = self.adb("shell", "pm", "path", GAME_PACKAGE)
        if "package:" not in out:
            self.log("Haikyuu Fly High ist in diesem Emulator nicht installiert.")
            self.set_status("game", "nicht installiert", BAD)
            path = self.ask_file_main(
                "Haikyuu Fly High ist in diesem LDPlayer nicht installiert.\n\n"
                "Hast du die Spieldatei (.apk, .xapk, .apks oder .apkm)? Dann wähle sie im nächsten "
                "Fenster aus, der Launcher installiert sie.")
            if not path or not self.install_game(Path(path)):
                return False
        return True

    def ask_file_main(self, question: str) -> str | None:
        """Yes/no, then a file dialog - on the Tk thread, from a worker thread."""
        from tkinter import filedialog, messagebox
        result, done = [None], threading.Event()

        def ask():
            try:
                if messagebox.askyesno("Haikyuu Fly High", question, parent=self.root):
                    result[0] = filedialog.askopenfilename(
                        parent=self.root, title="Haikyuu Fly High",
                        filetypes=[("APK / XAPK", "*.apk *.xapk *.apks *.apkm"), ("*", "*.*")]) or None
            finally:
                done.set()
        self.ui(ask)
        done.wait()
        return result[0]

    def install_game(self, path: Path) -> bool:
        """Install a single .apk or a split package (.xapk/.apks/.apkm: zip of apks, maybe obb)."""
        import tempfile
        import zipfile
        self.set_status("game", "installiere ...", WARN)
        work = None
        try:
            if path.suffix.lower() == ".apk":
                apks, obb = [path], None
            else:
                work = Path(tempfile.mkdtemp(prefix="hfh-apk-"))
                self.log(f"Entpacke {path.name} ...")
                with zipfile.ZipFile(path) as z:
                    z.extractall(work)
                apks = sorted(work.rglob("*.apk"), key=lambda a: (a.name != "base.apk", a.name))
                obb = next((d for d in work.rglob("obb") if d.is_dir()), None)
            if not apks:
                self.log(f"{path.name} enthält keine APK.")
                return False
            self.log(f"Installiere Haikyuu Fly High ({len(apks)} Teil(e)) – das dauert ein paar Minuten ...")
            cmd = ["install"] if len(apks) == 1 else ["install-multiple"]
            ok, out = self.adb(*cmd, "-r", *map(str, apks), timeout=1800)
            if not ok or "Success" not in out:
                self.log(f"Installation fehlgeschlagen: {out[-300:]}")
                self.set_status("game", "Fehler", BAD)
                return False
            if obb is not None:
                for pkg_dir in obb.iterdir():
                    self.adb("push", str(pkg_dir), "/sdcard/Android/obb/", timeout=1800)
            self.log("Haikyuu Fly High installiert.")
            self.set_status("game", "installiert", OK)
            return True
        except Exception as e:
            self.log(f"Installation fehlgeschlagen: {e}")
            self.set_status("game", "Fehler", BAD)
            return False
        finally:
            if work is not None:
                import shutil
                shutil.rmtree(work, ignore_errors=True)

    def wait_boot(self, index) -> bool:
        run([self.ld.adb_exe, "start-server"], timeout=30)
        end = time.time() + 240
        while time.time() < end:
            ok, out = self.adb("shell", "getprop", "sys.boot_completed", timeout=10)
            if ok and out.strip() == "1":
                return True
            if not ok and "not found" in out:
                # after a restart adb may list the instance only by its TCP address
                addr = f"127.0.0.1:{5555 + 2 * index}"
                ok2, out2 = run([self.ld.adb_exe, "connect", addr], timeout=10)
                if "connected" in out2:
                    self.serial = addr
            time.sleep(3)
        self.log("Emulator antwortet nicht über adb.")
        self.set_status("emu", "keine Verbindung", BAD)
        return False

    def ask_main(self, question: str) -> bool:
        """Yes/no dialog from a worker thread (runs on the Tk thread, waits for the answer)."""
        from tkinter import messagebox
        answer, done = [False], threading.Event()

        def ask():
            try:
                answer[0] = messagebox.askyesno("Haikyuu Fly High", question, parent=self.root)
            finally:
                done.set()
        self.ui(ask)
        done.wait()
        return answer[0]

    def setup_network(self):
        """adb reverse, CA certificate and HTTP proxy (needed in both modes)."""
        for port in (GAME_PORT, PROXY_PORT):
            ok, out = self.adb("reverse", f"tcp:{port}", f"tcp:{port}")
            if not ok:
                self.log(f"adb reverse tcp:{port} fehlgeschlagen: {out}")
                return False
        if not self.install_ca_cert():
            self.set_status("emu", "Zertifikat-Fehler", BAD)
            return False
        ok, out = self.adb("shell", "settings", "put", "global", "http_proxy",
                           f"127.0.0.1:{PROXY_PORT}")
        if not ok:
            self.log(f"Proxy im Emulator setzen fehlgeschlagen: {out}")
            return False
        self.set_status("emu", "verbunden", OK)
        return True

    def install_ca_cert(self):
        """Mount the mitmproxy CA into the emulator's system cert stores (lost on reboot)."""
        pem = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
        for _ in range(60):  # mitmproxy creates it on first start
            if pem.exists():
                break
            time.sleep(0.5)
        else:
            self.log("mitmproxy-Zertifikat fehlt (Proxy läuft nicht?).")
            return False
        import hashlib
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(pem.read_bytes())
        digest = hashlib.md5(cert.subject.public_bytes()).digest()
        name = f"{int.from_bytes(digest[:4], 'little'):08x}.0"

        sys_dir = "/system/etc/security/cacerts"
        ok, out = self.adb("shell", f"ls {sys_dir}/{name}")
        if ok and name in out:
            return True
        ok, out = self.adb("push", str(pem), "/data/local/tmp/hfh-ca.pem")
        if not ok:
            self.log(f"Zertifikat-Upload fehlgeschlagen: {out}")
            return False
        tmp_dir = "/data/local/tmp/cacerts"
        script = (
            f"mkdir -p {tmp_dir} && cp {sys_dir}/* {tmp_dir}/ && "
            f"cp /data/local/tmp/hfh-ca.pem {tmp_dir}/{name} && chmod 644 {tmp_dir}/* && "
            f"mount -o bind {tmp_dir} {sys_dir} && "
            f"(mount -o bind {tmp_dir} /apex/com.android.conscrypt/cacerts 2>/dev/null; true)"
        )
        ok, out = self.su(script)
        if ok:
            self.log(f"Proxy-Zertifikat im Emulator aktiv ({name}).")
        else:
            self.log(f"Zertifikat-Mount fehlgeschlagen: {out}")
        return ok

    def emulator_mode(self):
        """'private' if the iptables redirect is set, else 'garena' (None = unknown)."""
        ok, out = self.su("iptables -t nat -S OUTPUT")
        if not ok:
            return None
        for line in out.splitlines():
            if f"--dport {IN_GAME_PORT}" in line and f"127.0.0.1:{GAME_PORT}" in line:
                return "private"
        return "garena"

    def set_redirect(self, mode):
        """Add (private) or remove (garena) the iptables redirect. Returns True if changed."""
        before = self.emulator_mode()
        if mode == "private":
            if before != "private":
                self.remove_redirects()  # a rule to another port would win
                ok, out = self.su(f"iptables -t nat -A {IPTABLES_RULE}")
                if not ok:
                    self.log(f"iptables fehlgeschlagen: {out}")
        else:
            self.remove_redirects()
        after = self.emulator_mode()
        self.show_mode(after)
        if after == mode:
            self.log("Umleitung auf den Private Server aktiv." if mode == "private"
                     else "Umleitung entfernt – das Spiel verbindet sich mit Garena.")
        return before != after

    def remove_redirects(self):
        """Delete every DNAT rule for the game port."""
        ok, out = self.su("iptables -t nat -S OUTPUT")
        for line in out.splitlines():
            if f"--dport {IN_GAME_PORT}" in line and "DNAT" in line and line.startswith("-A "):
                self.su("iptables -t nat -D " + line[3:])

    def game_running(self):
        ok, out = self.adb("shell", "pidof", GAME_PACKAGE)
        return ok and out.strip() != ""

    def start_game(self, restart):
        if restart:
            self.adb("shell", "am", "force-stop", GAME_PACKAGE)
            time.sleep(1)
        ok, out = self.adb("shell", "am", "start", "-n", GAME_ACTIVITY)
        if ok and "Error" not in out:
            self.log("Spiel neu gestartet." if restart else "Spiel gestartet.")
            self.set_status("game", "läuft", OK)
        else:
            self.log(f"Spiel starten fehlgeschlagen: {out}")
            self.set_status("game", "Fehler", BAD)

    # --- jobs ----------------------------------------------------------------------

    def full_start(self):
        self.start_processes()
        if not self.ensure_emulator() or not self.setup_network():
            return
        changed = self.set_redirect(self.settings["mode"])
        running = self.game_running()
        self.start_game(restart=running and changed)
        if running and not changed:
            self.log("Spiel lief schon im richtigen Modus.")
        self.log(f"Fertig – viel Spaß! ({MODES[self.settings['mode']]})")

    def apply_mode_job(self):
        if self.ld is None or self.serial is None:
            return self.full_start()
        if not self.setup_network():
            return
        self.set_redirect(self.settings["mode"])
        self.start_game(restart=True)

    def restart_game_job(self):
        if self.ld is None or self.serial is None:
            return self.full_start()
        self.start_game(restart=True)

    # --- shutdown --------------------------------------------------------------------

    def on_close(self):
        self.closing = True
        for proc in (self.mitm_proc, self.server_proc):
            self.stop_proc(proc)
        if self.mitm_proc and self.ld and self.serial:
            # without our proxy a leftover proxy setting cuts the emulator off the internet
            self.adb("shell", "settings", "put", "global", "http_proxy", ":0", timeout=5)
        self.root.destroy()

    # --- backups of the save games (server/accounts) -----------------------------------------

    def backup_dir(self) -> Path:
        return self.base / "backups"

    def make_backup(self, reason: str) -> Path | None:
        """Zip server/accounts to backups/accounts-<time>.zip; keeps the newest BACKUP_KEEP."""
        src = self.base / "server" / "accounts"
        if not src.exists() or not any(src.iterdir()):
            return None
        import zipfile
        out_dir = self.backup_dir()
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"accounts-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(src.rglob("*")):
                if f.is_file() and not f.name.endswith(".tmp"):
                    z.write(f, f.relative_to(src.parent))
        for old in sorted(out_dir.glob("accounts-*.zip"))[:-BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
        self.log(f"Backup der Spielstände ({reason}): {out.name}")
        return out

    def backup_clicked(self):
        try:
            out = self.make_backup("von Hand")
        except Exception as e:
            self.log(f"Backup fehlgeschlagen: {e}")
            return
        if out is None:
            self.log("Keine Spielstände zum Sichern gefunden.")
            return
        try:
            os.startfile(str(self.backup_dir()))  # show the folder
        except Exception:
            pass

    # --- update check (zip packages only) ---------------------------------------------------

    def check_update(self):
        """Compare the package's version.json with the public repo; show a button if newer exists."""
        try:
            local = json.loads((Path(sys.executable).parent / "version.json").read_text(encoding="utf-8"))
        except Exception:
            return  # running from source or the own repo build: no update check
        if "--base" in sys.argv[1:]:
            return
        try:
            import urllib.request
            req = urllib.request.Request(f"https://api.github.com/repos/{UPDATE_REPO}/commits/main",
                                         headers={"User-Agent": "HFH-Launcher",
                                                  "Accept": "application/vnd.github+json"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=8) as r:
                remote = json.loads(r.read().decode("utf-8"))
        except Exception:
            return  # offline or rate-limited: try again next start
        if remote.get("sha") and remote["sha"] != local.get("commit"):
            when = remote.get("commit", {}).get("committer", {}).get("date", "")[:10]
            self.log(f"Neue Version verfügbar ({when}) – Knopf „Update verfügbar“.")
            self.ui(lambda: self.update_btn.pack(pady=(0, 10)))

    def offer_shortcut(self):
        """First start of a zip package: offer a desktop shortcut (asked once)."""
        pkg = self.package_dir()
        if pkg is None or self.settings.get("shortcut_asked") or "--update-now" in sys.argv[1:] \
                or "--no-emulator" in sys.argv[1:]:
            return
        self.settings["shortcut_asked"] = True
        save_settings(self.settings)
        from tkinter import messagebox
        if not messagebox.askyesno("Haikyuu Fly High", "Verknüpfung „Haikyuu Fly High“ auf dem Desktop anlegen?",
                                   parent=self.root):
            return
        exe = pkg / "HFH-Launcher.exe"
        ps = ("$d=[Environment]::GetFolderPath('Desktop');"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\Haikyuu Fly High.lnk\");"
              f"$s.TargetPath='{exe}';$s.WorkingDirectory='{pkg}';$s.IconLocation='{exe},0';"
              "$s.Description='Haikyuu Fly High - Launcher';$s.Save()")
        ok, out = run(["powershell", "-NoProfile", "-Command", ps], timeout=30)
        self.log("Desktop-Verknüpfung angelegt." if ok else f"Verknüpfung fehlgeschlagen: {out}")

    def open_update(self):
        from tkinter import messagebox
        if not self.package_dir():
            import webbrowser
            webbrowser.open(UPDATE_PAGE)
            return
        if messagebox.askyesno("Update", "Neue Version jetzt automatisch installieren?\n\n"
                               "Der Launcher lädt sie herunter (ca. 50 MB), beendet sich, ersetzt die "
                               "Programmdateien und startet neu. Deine Spielstände und Backups bleiben.",
                               parent=self.root):
            self.job(self.self_update)

    def package_dir(self) -> Path | None:
        """Folder of a zip package (frozen exe next to server/), None for source/own repo builds."""
        if not FROZEN or "--base" in sys.argv[1:]:
            return None
        d = Path(sys.executable).resolve().parent
        return d if (d / "server" / "server.py").exists() else None

    def self_update(self):
        """Download the matching release zip, then let a helper script swap the files after we exit."""
        import tempfile
        import urllib.request
        import zipfile
        pkg = self.package_dir()
        if pkg is None:
            return
        name = "HFH-FlyHigh-Admin" if (pkg / "server" / "admin.json").exists() else "HFH-FlyHigh"
        work = Path(tempfile.mkdtemp(prefix="hfh-update-"))
        zpath = work / f"{name}.zip"
        self.log(f"Lade {name}.zip ...")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(UPDATE_ZIP.format(name=name), headers={"User-Agent": "HFH-Launcher"})
            with opener.open(req, timeout=60) as r, open(zpath, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done, last = 0, 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total and done * 10 // total > last:
                        last = done * 10 // total
                        self.log(f"  {done * 100 // total} %")
            with zipfile.ZipFile(zpath) as z:
                z.extractall(work)
        except Exception as e:
            self.log(f"Update fehlgeschlagen: {e}")
            return
        new = work / name
        if not (new / "HFH-Launcher.exe").exists():
            self.log("Update fehlgeschlagen: die Zip enthält keinen Launcher.")
            return
        try:
            self.make_backup("vor dem Update")
        except Exception:
            pass
        bat = work / "update.bat"
        bat.write_text("\r\n".join([
            "@echo off",
            "chcp 65001 >nul",
            ":wait",
            f'tasklist /fi "PID eq {os.getpid()}" | find "{os.getpid()}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
            "timeout /t 1 /nobreak >nul",
            # copy everything except the save games, backups and own settings
            f'robocopy "{new}" "{pkg}" /E /NFL /NDL /NJH /NJS /XD "{new / "server" / "accounts"}" "{new / "backups"}" >nul',
            f'start "" "{pkg / "HFH-Launcher.exe"}" ' + " ".join(
                f'"{a}"' for a in sys.argv[1:] if a != "--update-now"),
            f'rmdir /s /q "{work}"',
        ]) + "\r\n", encoding="utf-8")
        self.log("Update geladen – der Launcher startet gleich neu.")
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=NO_WINDOW, close_fds=True)
        self.ui(self.on_close)

    def restore_clicked(self):
        """Pick a backup zip and put it back as server/accounts (current state is saved first)."""
        from tkinter import filedialog, messagebox
        self.backup_dir().mkdir(exist_ok=True)
        path = filedialog.askopenfilename(parent=self.root, title="Backup wiederherstellen",
                                          initialdir=str(self.backup_dir()),
                                          filetypes=[("Backups", "accounts-*.zip"), ("Zip", "*.zip")])
        if not path:
            return
        if self.server_proc is None and port_in_use(GAME_PORT):
            messagebox.showwarning("Wiederherstellen", "Der Server läuft gerade außerhalb des Launchers "
                                   "(Port 10104). Bitte diesen Server zuerst beenden, sonst überschreibt er "
                                   "die wiederhergestellten Spielstände.", parent=self.root)
            return
        if not messagebox.askyesno("Wiederherstellen", f"Spielstände aus {Path(path).name} wiederherstellen?\n\n"
                                   "Der aktuelle Stand wird vorher als Backup gesichert. Der Server wird "
                                   "dafür kurz neu gestartet.", parent=self.root):
            return
        self.job(self.restore_job, Path(path))

    def restore_job(self, path: Path):
        import zipfile
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.startswith("accounts/") and ".." not in n]
            if not names:
                self.log(f"{path.name} enthält keine Spielstände.")
                return
            self.make_backup("vor dem Wiederherstellen")
            self.stop_proc(self.server_proc)  # the server must not save over the restored files
            self.server_proc = None
            for _ in range(20):
                if not port_in_use(GAME_PORT):
                    break
                time.sleep(0.25)
            target = self.base / "server" / "accounts"
            import shutil
            if target.exists():
                shutil.rmtree(target)
            z.extractall(self.base / "server", members=names)
        self.log(f"Spielstände aus {path.name} wiederhergestellt.")
        self.start_processes()
        if self.ld is not None and self.serial is not None and self.game_running():
            self.start_game(restart=True)

    def run(self):
        try:
            self.make_backup("beim Start")
        except Exception as e:
            self.log(f"Backup beim Start fehlgeschlagen: {e}")
        if FROZEN:
            threading.Thread(target=self.check_update, daemon=True).start()
            self.root.after(2500, self.offer_shortcut)
        if "--update-now" in sys.argv[1:]:  # for tests: update without the dialog
            self.root.after(1500, lambda: self.job(self.self_update))
        if "--no-emulator" not in sys.argv[1:]:  # tests: launcher window only, no server/emulator
            self.root.after(300, lambda: self.job(self.full_start))
        self.root.mainloop()


def admin_unlocked(base: Path) -> dict:
    """Accounts with admin rights: password-unlocked (own server) and owner/granted (shared server)."""
    acc_dir = base / "server" / "accounts"
    out = {}
    try:
        out.update(json.loads((acc_dir / "admin_unlocked.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    try:
        st = json.loads((acc_dir / "admins.json").read_text(encoding="utf-8"))
        if st.get("owner"):
            out[str(st["owner"])] = "owner"
        out.update({g: "admin" for g in st.get("admins", {})})
    except Exception:
        pass
    return out


def main():
    argv = sys.argv[1:]
    if argv[:1] in (["--run-mitm"], ["--run-server"], ["--run-relay"]):
        # windowed .exe children have no console streams unless the parent piped them
        for name in ("stdout", "stderr"):
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    if argv[:1] == ["--run-mitm"]:
        return run_mitm(argv[1:])
    base = find_base(argv)
    if argv[:1] == ["--run-server"]:
        return run_server(base)
    if argv[:1] == ["--run-relay"]:
        rest = argv[1:]
        if "--base" in rest:
            i = rest.index("--base")
            del rest[i:i + 2]
        return run_relay(base, rest)
    Launcher(base).run()


if __name__ == "__main__":
    main()
