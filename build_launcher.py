"""
Build HFH-Launcher.exe and a shareable zip.

    python build_launcher.py [--data <repo>] [--shortcut] [--no-zip]

--data      folder with the server data (gamedata/cfg, server/proto_gen); default: this folder
--shortcut  create the desktop shortcut "Haikyuu Fly High" (uses the live repo as data folder)
--admin-pw  also build HFH-FlyHigh-Admin: Admin Mode, unlocked in game with this password
            (Settings -> gift code); the normal package has no Admin Mode
--server    preset the normal package's server address (host[:port] of a shared multiplayer
            server, launcher_defaults.json); players can still change it in the launcher
--jp DIR    also build the JP add-on HFH-FlyHigh-JP (zip): the server's JP tables (<data>/gamedata-jp:
            cfg + typed) and the patched client files DIR/Bundle (out/Install of build_client.py), which
            the launcher's proxy hands the client for "Inhalte: JP"; unpacked next to the launcher folder

Output:
  dist/HFH-FlyHigh/              ready-to-run folder (launcher + server snapshot + game config)
  dist/HFH-FlyHigh.zip           the same folder zipped, to pass on
The server code in the package is the last commit (git HEAD), not uncommitted edits.
"""
import argparse
import io
import os
import shutil
import subprocess
import time
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "HFH-Launcher"
PACKAGE = "HFH-FlyHigh"


APP_ICON = "res/mipmap-xxxhdpi-v4/app_icon.png"  # the game's launcher icon in base.apk


def make_icon(path: Path, data: Path):
    """launcher.ico + launcher.png from the game's app icon (apk/base.apk, not in git)."""
    from PIL import Image, ImageDraw
    apk = data / "apk" / "base.apk"
    if apk.exists():
        with zipfile.ZipFile(apk) as z:
            img = Image.open(io.BytesIO(z.read(APP_ICON))).convert("RGBA").resize(
                (256, 256), Image.LANCZOS)
        mask = Image.new("L", img.size, 0)  # rounded corners like on the phone
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 255, 255), radius=48, fill=255)
        img.putalpha(mask)
    else:
        print(f"Warnung: {apk} fehlt - nehme ein Ersatz-Icon")
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((8, 8, 248, 248), fill="#f5c518", outline="#1a1a2e", width=10)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((72, 72), Image.LANCZOS).save(path.with_suffix(".png"))


def pyinstaller(icon: Path):
    work = HERE / "build"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", NAME, "--icon", str(icon), "--add-data", f"{icon};.",
        "--add-data", f"{icon.with_suffix('.png')};.",
        "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
        "--specpath", str(work),
        "--collect-all", "mitmproxy", "--collect-all", "mitmproxy_rs",
        "--collect-all", "mitmproxy_windows", "--collect-submodules", "google.protobuf",
        # stdlib modules the server (loaded from .py files at runtime) needs
        *sum((["--hidden-import", m] for m in (
            "asyncio", "gzip", "struct", "logging", "json", "random", "math", "re",
            "dataclasses", "runpy", "hashlib", "collections", "itertools", "functools",
            "bisect", "heapq", "copy", "enum", "typing", "statistics", "zlib", "base64")), []),
        str(HERE / "launcher.py"),
    ]
    subprocess.run(cmd, check=True)
    return work / "dist" / NAME


def git_files(repo: Path, prefix: str):
    out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD", prefix],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def git_show(repo: Path, path: str, tries: int = 3) -> bytes:
    """A file of the last commit. Retried: on 26.11. one of the ~150 calls of the hourly public build
    crashed inside git (exit 0xC0000374, heap corruption) and broke the whole release; it did not
    happen again with the same file."""
    for attempt in range(tries):
        r = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{path}"], capture_output=True)
        if r.returncode == 0:
            return r.stdout
        print(f"git show HEAD:{path} failed (exit {r.returncode}), attempt {attempt + 1}/{tries}")
        time.sleep(1)
    raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)


def package(exe_dir: Path, data: Path, name: str = PACKAGE, admin_pw: str | None = None,
            server: str | None = None) -> Path:
    out = HERE / "dist" / name
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(exe_dir, out)
    # version of this package: the launcher compares it with the public repo to offer updates
    import json
    commit = subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    (out / "version.json").write_text(json.dumps({"commit": commit, "built": time.strftime("%Y-%m-%d %H:%M")}),
                                      encoding="utf-8")

    # server code + data from the last commit
    for rel in git_files(HERE, "server"):
        if rel.endswith("test_roundtrip.py") or rel.endswith("build_proto.py"):
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(git_show(HERE, rel))
    for rel in ("decoded/message_ids.json", "patch/mitm_redirect.py", "patch/patch_proxy.py"):
        if rel not in git_files(HERE, rel):  # patch_proxy.py: the JP proxy add-on (newer packages)
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(git_show(HERE, rel))

    # generated / extracted data (not in git)
    proto = data / "server" / "proto_gen"
    if not (proto / "haikyuu_pb2.py").exists():
        sys.exit(f"{proto} fehlt - erst 'python server/build_proto.py' ausführen")
    shutil.copytree(proto, out / "server" / "proto_gen",
                    ignore=shutil.ignore_patterns("__pycache__", "*.proto"))
    cfg = data / "gamedata" / "cfg"
    if not cfg.exists():
        sys.exit(f"{cfg} fehlt (tools_config_dump.py)")
    shutil.copytree(cfg, out / "gamedata" / "cfg")
    skill = data / "decoded" / "analysis" / "typed" / "skill.json"  # skills.py; without it skills are off
    if skill.exists():
        dest = out / "decoded" / "analysis" / "typed" / "skill.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(skill, dest)
    else:
        print(f"Warnung: {skill} fehlt - Skills im Match sind im Paket aus")
    shutil.copy(HERE / "LIESMICH.txt", out / "LIESMICH.txt")
    if (HERE / "README_EN.txt").exists():
        shutil.copy(HERE / "README_EN.txt", out / "README_EN.txt")
    if admin_pw:  # admin build: server/admin.json enables Admin Mode (password entered in game)
        import hashlib, json
        (out / "server" / "admin.json").write_text(json.dumps(
            {"pw_sha256": hashlib.sha256(admin_pw.strip().encode("utf-8")).hexdigest()}), encoding="utf-8")
    if server:  # multiplayer package: the launcher connects to this server instead of the local one
        import json
        (out / "launcher_defaults.json").write_text(json.dumps({"server": server.strip()}), encoding="utf-8")
    return out


JP_PACKAGE = f"{PACKAGE}-JP"
JP_README = """HFH-FlyHigh-JP - Inhalte der japanischen Fassung (Zusatz zum Launcher)

Dieses Zip NEBEN den Launcher-Ordner entpacken, so dass die Ordner nebeneinander liegen:
    ...\\HFH-FlyHigh\\          (oder HFH-FlyHigh-Admin)
    ...\\HFH-FlyHigh-JP\\
Dann im Launcher bei "Inhalte" auf "JP" klicken: Server und Proxy starten neu, das Spiel holt die
neuen Bilder und Tabellen (ca. 160 MB) und startet neu. Zurück auf "Global" lädt das Spiel die
Originaldateien wieder von Garena (ca. 150 MB).

Enthalten: gamedata-jp (Spieltabellen für den Server) und Install/Bundle (Spieldateien für den Client).

HFH-FlyHigh-JP - content of the Japanese version (add-on for the launcher)
Unpack this zip NEXT TO the launcher folder (both folders side by side), then click "JP" at
"Content" in the launcher. Back to "Global" the game downloads the original files again (~150 MB).
"""


def package_jp(data: Path, install: Path) -> Path:
    """The JP add-on: <data>/gamedata-jp (cfg + typed) and <install>/Bundle - game tables and game files
    only (no bundles.list, logs, backups or build tools of the folder it is built in)."""
    out = HERE / "dist" / JP_PACKAGE
    if out.exists():
        shutil.rmtree(out)
    src = data / "gamedata-jp"
    for part in ("cfg", "typed"):
        if not (src / part).is_dir():
            sys.exit(f"{src / part} fehlt (make_server_tables.py)")
        shutil.copytree(src / part, out / "gamedata-jp" / part, ignore=shutil.ignore_patterns("__pycache__"))
    if not (install / "Bundle").is_dir():
        sys.exit(f"{install / 'Bundle'} fehlt (build_client.py)")
    shutil.copytree(install / "Bundle", out / "Install" / "Bundle")
    (out / "LIESMICH-JP.txt").write_text(JP_README, encoding="utf-8")
    return out


def make_zip(folder: Path) -> Path:
    zpath = folder.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(folder.rglob("*")):
            rel = f.relative_to(folder)
            if f.is_file() and "__pycache__" not in rel.parts and "accounts" not in rel.parts[:2] \
                    and f.suffix != ".log":  # no local test state in the zip
                z.write(f, Path(folder.name) / f.relative_to(folder))
    return zpath


def desktop_shortcut(exe: Path, data: Path):
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\Haikyuu Fly High.lnk\");"
        f"$s.TargetPath='{exe}';"
        f"$s.Arguments='--base \"{data}\"';"
        f"$s.WorkingDirectory='{exe.parent}';"
        f"$s.IconLocation='{exe},0';"
        "$s.Description='Haikyuu Fly High - Private Server Launcher';"
        "$s.Save(); Write-Output \"$d\\Haikyuu Fly High.lnk\""
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    print("Verknüpfung:", r.stdout.strip() or r.stderr.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=HERE)
    ap.add_argument("--shortcut", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--admin-pw", help="also build HFH-FlyHigh-Admin with Admin Mode and this password")
    ap.add_argument("--server", help="preset server address host[:port] in the normal package")
    ap.add_argument("--jp", type=Path, help="also build the JP add-on from this out/Install folder")
    args = ap.parse_args()
    data = args.data.resolve()

    icon = HERE / "build" / "launcher.ico"
    icon.parent.mkdir(exist_ok=True)
    make_icon(icon, data)
    exe_dir = pyinstaller(icon)
    outs = [package(exe_dir, data, server=args.server)]
    if args.admin_pw:
        outs.append(package(exe_dir, data, f"{PACKAGE}-Admin", args.admin_pw))
    if args.jp:
        outs.append(package_jp(data, args.jp.resolve()))
    for out in outs:
        print("Ordner:", out)
        if not args.no_zip:
            print("Zip:", make_zip(out))
    if args.shortcut:
        desktop_shortcut(outs[-1] / f"{NAME}.exe", data)


if __name__ == "__main__":
    main()
