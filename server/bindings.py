"""
Token binding on a shared server: an account (gid) belongs to the Garena login that used it first.

The server cannot ask Garena whether a login token is valid, and the gid in LoginBySessionC2S is
just a number. Without this, anyone who knows another player's gid (or an admin's) could log in
as them. So on a multiplayer server (admin.multiplayer()) the first login of a gid stores a hash
of its token (field 7, stable across logins of the same Garena account); a later login with a
different token is refused (LOGIN_AUTH_FAILED) and noted as pending. The Garena token does change
when the player logs in to Garena anew, so a pending login can be approved:
  - in game by an admin: gift code "/rebind @player" (admin.command)
  - on the server PC (also for the owner's own account): python bindings.py approve <gid|name>
Only hashes are stored (accounts/bindings.json). Off on a player's own server; HFH_BIND_TOKENS=1/0
forces it on/off.

  python bindings.py list | approve <gid|name> | release <gid|name>
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
STORE = HERE / "accounts" / "bindings.json"


def active() -> bool:
    flag = os.environ.get("HFH_BIND_TOKENS")
    if flag is not None and flag != "":
        return flag != "0"
    import admin
    return admin.multiplayer()


def _hash(token: str) -> str:
    return hashlib.sha256(("hfh-bind:" + (token or "")).encode("utf-8")).hexdigest()


def load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save(data: dict):
    STORE.parent.mkdir(exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(STORE)


def check(gid: int, token: str) -> bool:
    """True if this login may use the account; binds a gid seen for the first time."""
    if not active() or not gid:
        return True
    data = load()
    h, now = _hash(token), int(time.time())
    entry = data.get(str(gid))
    if entry is None:
        data[str(gid)] = {"hash": h, "first": now, "last": now}
        save(data)
        return True
    if entry["hash"] == h:
        entry["last"] = now
        entry.pop("pending", None)
        save(data)
        return True
    entry["pending"] = {"hash": h, "time": now}
    save(data)
    return False


def approve(gid) -> bool:
    """Accept the pending (refused) login of this gid: its token becomes the bound one."""
    data = load()
    entry = data.get(str(gid))
    if not entry or "pending" not in entry:
        return False
    entry["hash"] = entry.pop("pending")["hash"]
    entry["last"] = int(time.time())
    save(data)
    return True


def release(gid) -> bool:
    """Forget the binding: the next login of this gid binds anew."""
    data = load()
    if str(gid) not in data:
        return False
    data.pop(str(gid))
    save(data)
    return True


def pending() -> dict:
    return {g: e["pending"] for g, e in load().items() if "pending" in e}


def _resolve(text: str) -> str | None:
    if text.isdigit():
        return text
    import admin
    return admin.find_player(text)


def main(argv):
    if not argv or argv[0] == "list":
        names = {}
        try:
            import admin
            names = admin.player_names()
        except Exception:
            pass
        for g, e in sorted(load().items()):
            state = "WARTET auf Freigabe" if "pending" in e else "gebunden"
            print(f"{g:>14}  {names.get(g, ''):<20} {state}  zuletzt {time.strftime('%d.%m. %H:%M', time.localtime(e['last']))}")
        return
    if len(argv) < 2 or argv[0] not in ("approve", "release"):
        sys.exit(__doc__)
    gid = _resolve(" ".join(argv[1:]))
    if gid is None:
        sys.exit("unbekannter Spieler")
    ok = approve(gid) if argv[0] == "approve" else release(gid)
    print(f"{argv[0]} {gid}: {'ok' if ok else 'nichts zu tun'}")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main(sys.argv[1:])
