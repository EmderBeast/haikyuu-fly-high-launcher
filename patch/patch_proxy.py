"""mitmproxy addon for the JP edition: deliver patched client files through the game's own update check.

The Garena client asks https://dl-hiq.garena.com/live/OBT/Hotfix/Patch/Android/<ver>/<res>/bundles.list
(path, md5, size per line) at every start and downloads every file whose md5 differs from its local copy
from .../<res>/<url-encoded path>. This addon
  - rewrites that list: md5 / size of the files in PATCH_DIR, new files appended
  - answers the download of those files from PATCH_DIR
so the client fetches the patched files by itself and keeps them.

  mitmdump -s <game-capture-setup>/patch/mitm_redirect.py -s patch_proxy.py --mode regular@8083 --set ssl_insecure=true
PATCH_DIR = HFH_PATCH_DIR or ./out/Install (as built by build_client.py: Bundle/..., bundles.list is not used).
The patched files (table bundle cfg.ab with the JP rows, asset index, manifest, JP picture bundles) are
game data and not part of this repository; the server must run on the same tables (edition.py, "jp").
"""
import hashlib
import os
from pathlib import Path
from urllib.parse import unquote

from mitmproxy import http

PATCH_DIR = Path(os.environ.get("HFH_PATCH_DIR") or Path(__file__).parent / "out" / "Install")
CDN = "dl-hiq.garena.com"
MARK = "/Hotfix/Patch/"

FILES: dict[str, Path] = {}
META: dict[str, tuple[str, int]] = {}
for p in sorted(PATCH_DIR.rglob("*")):
    if p.is_file() and p.name != "bundles.list":
        key = p.relative_to(PATCH_DIR).as_posix()
        data = p.read_bytes()
        FILES[key] = p
        META[key] = (hashlib.md5(data).hexdigest(), len(data))
print(f"[HFH-PATCH] {len(FILES)} patched files from {PATCH_DIR}")


def _name(flow: http.HTTPFlow) -> str | None:
    if CDN not in flow.request.pretty_host or MARK not in flow.request.path:
        return None
    return unquote(flow.request.path.split("?")[0].rsplit("/", 1)[-1])


def request(flow: http.HTTPFlow) -> None:
    name = _name(flow)
    if name in FILES:
        data = FILES[name].read_bytes()
        flow.response = http.Response.make(200, data, {"Content-Type": "application/octet-stream"})
        print(f"[HFH-PATCH] served {name} ({len(data)} bytes)")


def response(flow: http.HTTPFlow) -> None:
    if _name(flow) != "bundles.list" or flow.response is None or flow.response.status_code != 200:
        return
    text = flow.response.content.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = [line.split("\t") for line in text.split(nl) if line]
    seen = set()
    for line in lines:
        if line[0] in META:
            line[1], line[2] = META[line[0]][0], str(META[line[0]][1])
            seen.add(line[0])
    added = [[k, m, str(s)] for k, (m, s) in META.items() if k not in seen]
    flow.response.content = (nl.join("\t".join(line) for line in lines + added) + nl).encode("utf-8")
    print(f"[HFH-PATCH] bundles.list: {len(seen)} changed, {len(added)} added")
