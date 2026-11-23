"""
mitmproxy addon for Haikyuu Fly High.

The emulator's HTTP(S) traffic goes through this proxy (launcher.py sets it up).
The game's TCP connection (port 10104) is redirected to the private server by
an iptables rule inside the emulator, not by this proxy. This addon only:

- logs requests to Garena / the (shut down) CDN dl-hiq.garena.com,
- in "private" mode, rewrites the old hardcoded game server IP in Garena auth
  responses to 127.0.0.1 (reaches the PC via `adb reverse tcp:10104`).

In "garena" mode everything passes through unchanged. The mode is read from the
launcher's settings file (HFH_SETTINGS env var, default
%LOCALAPPDATA%\\HFH-Launcher\\settings.json); without that file it is "private".

Usage:
  mitmdump -s patch/mitm_redirect.py --mode regular@8082 --set ssl_insecure=true
"""
import json
import os
from pathlib import Path

from mitmproxy import http

PRIVATE_SERVER_IP = "127.0.0.1"
OLD_SERVER_IPS = ["43.130.174.32"]
CDN_HOSTS = ["dl-hiq.garena.com"]

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


def request(flow: http.HTTPFlow) -> None:
    if any(cdn in flow.request.pretty_host for cdn in CDN_HOSTS):
        print(f"[HFH-CDN] {flow.request.method} {flow.request.pretty_url}")


def response(flow: http.HTTPFlow) -> None:
    if flow.response is None or flow.response.content is None:
        return

    host = flow.request.pretty_host
    url = flow.request.pretty_url
    if "garena" in host or "hiq" in host:
        print(f"[HFH] {flow.response.status_code} {url} ({len(flow.response.content)} bytes)")

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
