"""
mitmproxy addon for Haikyuu Fly High.

The emulator's HTTP(S) traffic goes through this proxy (launcher.py sets it up).
The game's TCP connection (port 10104) is redirected to the private server by
an iptables rule inside the emulator, not by this proxy. This addon only:

- logs requests to Garena / the (shut down) CDN dl-hiq.garena.com,
- downloads the CDN's .ab bundles itself (with retries and resume, the CDN often breaks off big
  files; checked against the md5 in bundles.list) into patch/cdn-cache/ and answers the game from
  there; later downloads come from the cache,
- in "private" mode, rewrites the old hardcoded game server IP in Garena auth
  responses to 127.0.0.1 (reaches the PC via `adb reverse tcp:10104`).

In "garena" mode everything passes through unchanged. The mode is read from the
launcher's settings file (HFH_SETTINGS env var, default
%LOCALAPPDATA%\\HFH-Launcher\\settings.json); without that file it is "private".

Usage:
  mitmdump -s patch/mitm_redirect.py --mode regular@8082 --set ssl_insecure=true
"""
import asyncio
import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path
from urllib.parse import unquote

from mitmproxy import http

PRIVATE_SERVER_IP = "127.0.0.1"
OLD_SERVER_IPS = ["43.130.174.32"]
CDN_HOSTS = ["dl-hiq.garena.com"]
CDN_CACHE = Path(os.environ.get("HFH_CDN_CACHE") or Path(__file__).parent / "cdn-cache")
LOCKS: dict[Path, asyncio.Lock] = {}  # one download per file, parallel requests wait for it
EXPECTED: dict[str, str] = {}  # md5 per bundle from the CDN's bundles.list ("Bundle/x/y.ab" -> md5)

SETTINGS = Path(os.environ.get("HFH_SETTINGS") or
                Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HFH-Launcher" / "settings.json")
_cache = {"mtime": None, "mode": "private"}


def mode() -> str:
    try:
        mtime = SETTINGS.stat().st_mtime
        if mtime != _cache["mtime"]:
            _cache["mode"] = json.loads(SETTINGS.read_text(encoding="utf-8")).get("mode", "private")
            _cache["mtime"] = mtime
    except Exception:
        pass
    return _cache["mode"]


def _cdn_file(flow: http.HTTPFlow) -> Path | None:
    """Cache path of a CDN bundle download (.ab), None for anything else (lists, other hosts)."""
    if flow.request.method != "GET" or not any(c in flow.request.pretty_host for c in CDN_HOSTS):
        return None
    path = unquote(flow.request.path.split("?")[0]).lstrip("/")
    if "/Hotfix/Patch/" not in "/" + path or not path.endswith(".ab") or ".." in path:
        return None
    patch_dir = os.environ.get("HFH_PATCH_DIR")  # JP: patch_proxy.py answers its own files
    if patch_dir and "/Bundle/" in path and (Path(patch_dir) / ("Bundle/" + path.split("/Bundle/", 1)[1])).exists():
        return None
    return CDN_CACHE.joinpath(*path.split("/"))


def _fetch(url: str, dest: Path, agent: str, md5: str | None = None) -> bool:
    """Download url to dest, resuming after the CDN breaks off; checked against the list's md5
    (a wrong file is downloaded again from the start, 3 times at most)."""
    for full in range(3):
        if _fetch_once(url, dest, agent):
            if not md5 or hashlib.md5(dest.read_bytes()).hexdigest() == md5:
                return True
            print(f"[HFH-CDN] {dest.name}: falsche Prüfsumme, lade neu ({full + 1}/3)")
            dest.replace(dest.with_name(dest.name + ".bad"))
    return False


def _fetch_once(url: str, dest: Path, agent: str) -> bool:
    """Download url to dest, resuming after the CDN breaks off (up to 10 tries)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if part.exists():
        part.replace(part.with_name(part.name + ".old"))
    for attempt in range(1, 11):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": agent or "UnityPlayer"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                if have and r.status != 206:  # no resume support: start over
                    have = 0
                length = r.headers.get("Content-Length")
                total = have + int(length) if length else None
                with open(part, "ab" if have else "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
            if total is None or part.stat().st_size >= total:
                part.replace(dest)
                return True
            print(f"[HFH-CDN] {dest.name}: abgebrochen bei {part.stat().st_size} von {total} Bytes, Versuch {attempt}")
        except Exception as e:
            print(f"[HFH-CDN] {dest.name}: {e}, Versuch {attempt}")
    return False


def _fix_list(flow: http.HTTPFlow) -> None:
    """bundles.list: note the md5 of every bundle, downloads from the CDN are checked against it."""
    text = flow.response.content.decode("utf-8", errors="replace")
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[0].endswith(".ab"):
            EXPECTED[cols[0]] = cols[1]


async def request(flow: http.HTTPFlow) -> None:
    if any(cdn in flow.request.pretty_host for cdn in CDN_HOSTS):
        print(f"[HFH-CDN] {flow.request.method} {flow.request.pretty_url}")
    dest = _cdn_file(flow)
    if dest is None:
        return
    md5 = EXPECTED.get("Bundle/" + dest.as_posix().split("/Bundle/", 1)[-1])
    async with LOCKS.setdefault(dest, asyncio.Lock()):
        try:
            if dest.exists() and md5 and hashlib.md5(dest.read_bytes()).hexdigest() != md5:
                dest.replace(dest.with_name(dest.name + ".bad"))  # an older, broken download
            if not dest.exists():
                await asyncio.to_thread(_fetch, flow.request.pretty_url, dest,
                                        flow.request.headers.get("User-Agent", ""), md5)
        except OSError as e:
            print(f"[HFH-CDN] {dest.name}: {e}")
    if dest.exists():
        data = dest.read_bytes()
        flow.response = http.Response.make(200, data, {"Content-Type": "application/octet-stream"})
        print(f"[HFH-CDN] {dest.name} aus dem Cache ({len(data)} Bytes)")


def response(flow: http.HTTPFlow) -> None:
    if flow.response is None or flow.response.content is None:
        return

    host = flow.request.pretty_host
    url = flow.request.pretty_url
    if "garena" in host or "hiq" in host:
        print(f"[HFH] {flow.response.status_code} {url} ({len(flow.response.content)} bytes)")
    if url.split("?")[0].endswith("/bundles.list") and flow.response.status_code == 200:  # for checking md5s
        try:
            CDN_CACHE.mkdir(parents=True, exist_ok=True)
            (CDN_CACHE / "last-bundles.list").write_bytes(flow.response.content)
        except OSError:
            pass
        _fix_list(flow)

    if mode() != "private":
        return
    content_type = flow.response.headers.get("content-type", "")
    if "json" not in content_type and "protobuf" not in content_type:
        return

    for old in OLD_SERVER_IPS:
        raw = old.encode()
        if raw not in flow.response.content:
            continue
        if "json" in content_type:
            flow.response.content = flow.response.content.replace(raw, PRIVATE_SERVER_IP.encode())
        else:  # keep binary lengths intact
            flow.response.content = flow.response.content.replace(
                raw, PRIVATE_SERVER_IP.encode().ljust(len(raw), b"\x00"))
        print(f"[HFH] Server-IP in {url} -> {PRIVATE_SERVER_IP}")
