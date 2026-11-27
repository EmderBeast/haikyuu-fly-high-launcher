"""
Publish a cleaned snapshot of this (private) repo to the PUBLIC repo and keep its release
"launcher-latest" (normal + admin zip) up to date.

    python publish_release.py            # only if origin/main changed since the last publish
    python publish_release.py --force    # always

Steps (never touches this working checkout, which other people edit):
1. export: the files in PUBLIC_FILES at origin/main go into the public clone ../hfh-public;
   in server/data/login_sync.json and replies.json every message that carries other players'
   data (friends, chat, club, rankings, visitors, arena opponents, mail) is emptied; README.md
   comes from public_readme.md. Commit + push when something changed.
2. build: build_launcher.py runs in the public clone (so the zips contain exactly the public
   files), with the data that is not in git (gamedata/cfg, proto_gen, skill.json, apk icon)
   taken from this checkout.
3. release: tag launcher-latest -> public HEAD, zips uploaded (replaced), local copies next to
   the repo folder.

JP add-on (HFH-FlyHigh-JP.zip, the content of the Japanese version for "Inhalte: JP"): built from
gamedata-jp of this checkout and the patched client files (release.json "jp_install" = the out/Install
of the client work; without it there is no JP add-on); built and uploaded only when those files changed (hash), since the
zip is large and changes rarely.

Local, not in git: ../hfh-release/release.json (next to the repo folder)
  {"admin_pw": "..."}            admin password for the admin zip (required)
  {"jp_install": "..."}          folder with Bundle/ of the patched client (optional)
  {"last_sha": "...", "jp_hash": "..."}   written by this script
Log: ../hfh-release/release.log. Run hourly by the Windows task "HFH Launcher Release".
"""
import gzip
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLIC_REPO = "EmderBeast/haikyuu-fly-high-launcher"
PUBLIC_DIR = HERE.parent / "hfh-public"
TAG = "launcher-latest"
TITLE = "Launcher (neueste Version)"
APP_DIR = HERE.parent / "hfh-release"  # next to the repo, outside git (AppData can be app-virtualized)
CONFIG = APP_DIR / "release.json"
LOG = APP_DIR / "release.log"
ZIPS = ["HFH-FlyHigh.zip", "HFH-FlyHigh-Admin.zip"]
JP_ZIP = "HFH-FlyHigh-JP.zip"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# what the public repo contains (paths at origin/main); server/*.py is added automatically
PUBLIC_FILES = [
    "launcher.py", "launcher_i18n.py", "admin_mode.py", "build_launcher.py", "publish_release.py", "LIESMICH.txt", "README_EN.txt",
    "patch/mitm_redirect.py", "patch/patch_proxy.py", "decoded/message_ids.json", "decoded/proto/haikyuu.proto",
    "server/data/anim_table.json", "server/data/rallies.bin",
    "server/data/login_sync.json", "server/data/replies.json",
]
SERVER_SKIP = {"server/test_roundtrip.py", "server/test_pvp.py", "server/calibrate.py",
               "server/buffdiff.py"}
# messages with other players' names / texts: emptied in the public copy
SCRUB = {
    "0x0C01",  # mail
    "0x1001", "0x1002", "0x1006", "0x100C",  # friends
    "0x1B01", "0x1B02", "0x1B03", "0x1B06", "0x1B07",  # chat
    "0x2E01", "0x2E11", "0x2E12", "0x2E1B", "0x2E1D",  # club
    "0x1301", "0x1302", "0x1303", "0x1404",  # rankings
    "0x2302", "0x2304", "0x2308", "0x230E", "0x230F", "0x2314", "0x2316", "0x2317",
    "0x2318", "0x2319", "0x231A", "0x231F", "0x2320", "0x2321", "0x2322",  # home visits
    "0x1401", "0x2703", "0x2704", "0x2705", "0x2708",  # arena info, opponents, records
    "0x4A06",  # player lists
    "0x210D",  # other players' profiles
}
# scrubbed messages that are lists of players: their names are also masked elsewhere
NAME_SOURCES = {"0x1001", "0x1002", "0x2E12", "0x1301", "0x1302", "0x1404", "0x1401", "0x2703",
                "0x2704", "0x2705", "0x2708", "0x4A06", "0x2301", "0x1B06", "0x210D"}
EMPTY_BODY = {"flags": 1, "body_hex": "00"}
# messages a new player's login needs: only these top-level fields stay (the rest has other players)
# HomeInfoS2C: the client reads base info unchecked - emptied, a starter account hangs at 41 % loading
# ("No HomesteadBaseData"); 4/5/7/8/9/14/16 = visitors, messages, likes (same cut as server/starter.py)
PARTIAL = {"0x2301": {1, 2, 3, 6, 10, 11, 12, 13}}
PUBLIC_GITIGNORE = """__pycache__/
*.pyc
*.log
build/
dist/
gamedata/
server/accounts/
server/proto_gen/
server/admin.json
server/owners.json
server/starter_debug.json
launcher_defaults.json
"""


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    if sys.stdout:  # None under pythonw (Task Scheduler)
        print(line, flush=True)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(*cmd, cwd=HERE, check=True, timeout=1800):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def show(sha: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{sha}:{path}"], cwd=HERE, capture_output=True,
                          check=True, creationflags=NO_WINDOW).stdout


def _pb(m) -> bytes:
    body = bytes.fromhex(m["body_hex"])
    return gzip.decompress(body) if m["flags"] & 0x02 else body


def player_names(messages) -> set[bytes]:
    """Name-like strings from the scrubbed messages (friends, club, rankings ...)."""
    keep = {b"Emder", b"https", b"Discord", b"discord"}
    names = set()
    for m in messages:
        if m["msg_id"] in NAME_SOURCES:
            names |= {x for x in re.findall(rb"[A-Za-z0-9_.]{5,}", _pb(m)) if not x.isdigit()}
    return names - keep


def _mask(m, names) -> dict:
    """Replace other players' names inside a kept message by same-length placeholders."""
    pb = _pb(m)
    new = pb
    for n in sorted(names, key=len, reverse=True):
        if n in new:
            new = new.replace(n, (b"Player" + b"0" * len(n))[:len(n)])
    if new == pb:
        return m
    body = gzip.compress(new) if m["flags"] & 0x02 else new
    return {**m, "body_hex": body.hex()}


def _keep_fields(m, keep: set[int]) -> dict:
    """Message reduced to the given top-level protobuf fields (body = 0x00 + protobuf)."""
    sys.path.insert(0, str(HERE / "server"))
    import wire
    pb = _pb(m)
    head, pb = (pb[:1], pb[1:]) if pb[:1] == b"\x00" else (b"", pb)
    new = head + wire.build([f for f in wire.parse(pb) if f[0] in keep])
    return {**m, "flags": m["flags"] & ~0x02, "body_hex": new.hex()}


def _clean(msg_id: str, m, names) -> dict:
    if msg_id in SCRUB:
        return {**m, **EMPTY_BODY}
    if msg_id in PARTIAL:
        m = _keep_fields(m, PARTIAL[msg_id])
    return _mask(m, names)


def scrub(path: str, data: bytes, names: set[bytes]) -> bytes:
    if path == "server/data/login_sync.json":
        msgs = json.loads(data)
        msgs = [_clean(m["msg_id"], m, names) for m in msgs]
        return json.dumps(msgs, indent=1).encode("utf-8")
    if path == "server/data/replies.json":
        replies = json.loads(data)
        replies = {k: _clean(k, m, names) for k, m in replies.items()}
        return json.dumps(replies, indent=1).encode("utf-8")
    return data


def export(sha: str, subject: str) -> bool:
    """Write the cleaned snapshot into the public clone; True if a commit was pushed."""
    if not (PUBLIC_DIR / ".git").exists():
        sh("gh", "repo", "clone", PUBLIC_REPO, str(PUBLIC_DIR), cwd=HERE.parent)
    sh("git", "fetch", "-q", "origin", cwd=PUBLIC_DIR, check=False)
    has_head = subprocess.run(["git", "rev-parse", "-q", "--verify", "origin/main"], cwd=PUBLIC_DIR,
                              capture_output=True, creationflags=NO_WINDOW).returncode == 0
    if has_head:
        sh("git", "checkout", "-q", "-B", "main", "origin/main", cwd=PUBLIC_DIR)
    for p in PUBLIC_DIR.iterdir():  # replace the tracked content completely
        if p.name in (".git", "build", "dist"):
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    files = sh("git", "ls-tree", "-r", "--name-only", sha, "server").splitlines()
    files = [f for f in files if f.endswith(".py") and f.count("/") == 1 and f not in SERVER_SKIP]
    names = player_names(json.loads(show(sha, "server/data/login_sync.json"))
                         + list(json.loads(show(sha, "server/data/replies.json")).values()))
    for path in sorted(set(PUBLIC_FILES + files)):
        dest = PUBLIC_DIR / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(scrub(path, show(sha, path), names))
    (PUBLIC_DIR / "README.md").write_bytes(show(sha, "public_readme.md"))
    (PUBLIC_DIR / ".gitignore").write_text(PUBLIC_GITIGNORE, encoding="utf-8")
    sh("git", "add", "-A", cwd=PUBLIC_DIR)
    if not sh("git", "status", "--porcelain", cwd=PUBLIC_DIR):
        return False
    sh("git", "-c", "user.name=Emder", "-c", "user.email=emder@users.noreply.github.com",
       "commit", "-q", "-m", f"Stand {sha[:7]}: {subject}", cwd=PUBLIC_DIR)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=PUBLIC_DIR)
    return True


def jp_hash(install: Path) -> str | None:
    """Content hash of what goes into the JP add-on; None when its files are not there."""
    import hashlib
    parts = [install / "Bundle", HERE / "gamedata-jp" / "cfg", HERE / "gamedata-jp" / "typed"]
    if not all(d.is_dir() for d in parts):
        return None
    h = hashlib.md5()
    for d in parts:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                h.update(f.relative_to(d.parent).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()


def notes(sha: str, subject: str) -> str:
    return f"""Ein-Klick-Launcher für Haikyuu Fly High auf dem eigenen Server (LDPlayer 9/14, kein Python nötig).
Wird automatisch neu gebaut, sobald es eine neue Version gibt.

**Downloads**
- `HFH-FlyHigh.zip` – normale Version: Launcher + Server + Spieldaten, Feld für eine Server-Adresse (leer = eigener Server).
- `HFH-FlyHigh-Admin.zip` – zusätzlich **Admin Mode**: im Spiel Einstellungen → Gift Code → Admin-Passwort, danach im Launcher Level, Währungen, alle Charaktere, Max-Karten, Gegenstände und Potentials einstellen; eigenen Trainer bauen (Reiter „Trainer“) und „ALLES freischalten“ (alle Charaktere, Trainer, Cheers, Kosmetik).
- `HFH-FlyHigh-JP.zip` – **Zusatz: Inhalte der japanischen Fassung**, u. a. vier neue Spieler: Kiyoomi Sakusa (WB), Motoya Komori (WB), Aran Ojiro (WS), Koshi Sugawara (WS), Balance-Stand vom 26.11.2026. Neben den Launcher-Ordner entpacken, dann im Launcher bei „Inhalte“ auf **JP** klicken; das Spiel lädt die neuen Dateien (ca. 160 MB) und startet neu. Zurück auf **Global** lädt es die Originaldateien wieder von Garena (ca. 150 MB). Namen und Fähigkeiten der neuen Spieler sind auf Deutsch, die neuen Karten gibt es über den Admin Mode.

**Start:** Zip entpacken (nicht nach „C:\\Programme“), `HFH-Launcher.exe` starten, Anleitung in `LIESMICH.txt`.
Voraussetzung: LDPlayer 9 oder 14 mit installiertem Haikyuu Fly High, Root an (schaltet der Launcher selbst ein, wenn er den Emulator startet).

**Stand:** `{sha[:7]}` – {subject} (gebaut {time.strftime("%d.%m.%Y %H:%M")})

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""


def main():
    force = "--force" in sys.argv
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if not cfg.get("admin_pw"):
        log(f"admin_pw fehlt in {CONFIG}")
        return 1

    sh("git", "fetch", "-q", "origin")
    sha = sh("git", "rev-parse", "origin/main")
    if sha == cfg.get("last_sha") and not force:
        return 0
    subject = sh("git", "log", "-1", "--format=%s", sha)
    log(f"neue Version {sha[:7]}: {subject}")

    log("öffentliches Repo aktualisiert" if export(sha, subject) else "öffentliches Repo unverändert")
    install = Path(cfg["jp_install"]) if cfg.get("jp_install") else None
    new_jp = jp_hash(install) if install else None
    jp = new_jp is not None and (new_jp != cfg.get("jp_hash") or force)
    if new_jp is None:
        log(f"JP-Zusatz übersprungen: jp_install in {CONFIG.name} fehlt, oder Bundle / gamedata-jp fehlt")
    extra = ["--jp", str(install)] if jp else []
    out = sh(sys.executable.replace("pythonw.exe", "python.exe"), "build_launcher.py",
             "--data", str(HERE), "--admin-pw", cfg["admin_pw"], *extra, cwd=PUBLIC_DIR, timeout=3600)
    log(out.splitlines()[-1] if out else "build ok")
    zips = [PUBLIC_DIR / "dist" / z for z in ZIPS + ([JP_ZIP] if jp else [])]
    for z in zips:
        if not z.exists():
            raise RuntimeError(f"{z} fehlt nach dem Build")

    head = sh("git", "rev-parse", "HEAD", cwd=PUBLIC_DIR)
    sh("git", "push", "-q", "--force", "origin", f"{head}:refs/tags/{TAG}", cwd=PUBLIC_DIR)
    body = APP_DIR / "release_notes.md"
    body.write_text(notes(sha, subject), encoding="utf-8")
    if subprocess.run(["gh", "release", "view", TAG, "-R", PUBLIC_REPO], capture_output=True,
                      creationflags=NO_WINDOW).returncode == 0:
        sh("gh", "release", "upload", TAG, *map(str, zips), "--clobber", "-R", PUBLIC_REPO, timeout=3600)
        sh("gh", "release", "edit", TAG, "--title", TITLE, "--notes-file", str(body), "-R", PUBLIC_REPO)
    else:
        sh("gh", "release", "create", TAG, *map(str, zips), "--verify-tag", "--latest",
           "--title", TITLE, "--notes-file", str(body), "-R", PUBLIC_REPO, timeout=3600)
    for z in zips:  # local copies next to the repo
        shutil.copy(z, HERE.parent / z.name)

    cfg["last_sha"] = sha
    if jp:
        cfg["jp_hash"] = new_jp
        log(f"JP-Zusatz neu hochgeladen ({(PUBLIC_DIR / 'dist' / JP_ZIP).stat().st_size // 2**20} MB)")
    CONFIG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    log(f"veröffentlicht: {sha[:7]} -> {PUBLIC_REPO}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"FEHLER: {e}")
        sys.exit(1)
