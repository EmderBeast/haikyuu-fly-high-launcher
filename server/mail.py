"""
Mailbox: the in-game mail (0x0C01-0x0C05) with real attachments, for season / daily rank rewards.

  0x0C01 MailInfoS2C{mail_list}           whole box (sent after the login sync, replaces the sync's)
  0x0C02 MailUpdateS2C{mail_list}         new mail for a player who is online
  0x0C03 MailReadC2S{inst_id}             UNREAD -> READED_UNGET (with items) / DONE
  0x0C04 MailGetItemC2S{inst_id,get_all}  attachments credited, mail DONE; reply lists the ids
  0x0C05 MailRemoveC2S{inst_id,remove_all} removes mails without pending attachments

Mails are built from mailtemplate (Mailtitle / Mailtext as [LID]/[FID] strings, Savedays) like
Garena's (captured: sender [LID:1024324], content_args e.g. [rank, date]). The captured owner
account keeps its 17 unclaimed captured mails (their state is tracked here). Stored in pvp.json.
"""
import time

import matches
import pvp
from accounts import CAPTURED

P = matches.P

MSG_INFO, MSG_UPDATE, MSG_READ, MSG_GET, MSG_REMOVE = 0x0C01, 0x0C02, 0x0C03, 0x0C04, 0x0C05
SENDER = "[LID:1024324]"  # the system sender of the captured mails
TEMPLATES = {r["ID"]: r for r in pvp._cfg("mailtemplate")}
ONLINE: dict[int, object] = {}  # gid -> client, for pushing new mail

_seq = [0]


def _box(gid: int, acc=None) -> dict:
    boxes = pvp.state().setdefault("mail", {})
    b = boxes.get(str(gid))
    if b is None:
        b = boxes[str(gid)] = {"mails": [], "captured": {}}
    return b


def _captured_mails(acc) -> list:
    if acc is None or acc.b is not CAPTURED:
        return []
    try:
        return list(P.MailInfoS2C.FromString(CAPTURED.pb("0x0C01")).mail_list)
    except Exception:
        return []


def _new_id() -> int:
    _seq[0] = (_seq[0] + 1) % 1000
    return int(time.time() * 1000) * 1000 + _seq[0]


def items_of(spec) -> list[tuple[int, int]]:
    """'conf:n' strings, [conf, n] pairs or kv lists -> [(conf, n)]."""
    if not spec:
        return []
    if isinstance(spec, list) and spec and isinstance(spec[0], str):
        return [tuple(int(x) for x in s.split(":")) for s in spec if ":" in s]
    import teamcomp
    return teamcomp.kv(spec)


def make(template: int, items: list[tuple[int, int]] | None = None, args: list | None = None,
         now: float | None = None) -> dict:
    t = TEMPLATES.get(template, {})
    now = int(time.time() if now is None else now)
    items = items if items is not None else items_of(t.get("Reward"))
    return {"id": _new_id(), "template": template, "title": t.get("Mailtitle", ""),
            "content": t.get("Mailtext", ""), "items": [list(i) for i in items],
            "args": [str(a) for a in (args or [])], "send": now,
            "end": now + int(t.get("Savedays") or 30) * 86400,
            "state": P.MAIL_UNREAD}


def _msg(m: dict) -> P.Mail:
    out = P.Mail(inst_id=m["id"], sender=SENDER, title=m["title"], content=m["content"],
                 send_time=m["send"], end_time=m["end"], state=m["state"])
    for conf, n in m["items"]:
        out.item.add(conf_id=conf, number=n)
    out.content_args.extend(m["args"])
    return out


def mail_list(gid: int, acc=None) -> list[P.Mail]:
    b = _box(gid, acc)
    now = time.time()
    out = []
    for m in _captured_mails(acc):
        st = b["captured"].get(str(m.inst_id))
        if st == "removed" or (m.end_time and m.end_time < now):
            continue
        c = P.Mail()
        c.CopyFrom(m)
        if st is not None:
            c.state = st
        out.append(c)
    b["mails"] = [m for m in b["mails"] if m["end"] >= now]
    out += [_msg(m) for m in b["mails"]]
    return out


def send(gid: int, mail: dict):
    """Put a mail into a player's box (pushed at once if they are online)."""
    _box(gid)["mails"].append(mail)
    pvp.save()
    client = ONLINE.get(int(gid))
    if client is not None:
        import asyncio
        upd = P.MailUpdateS2C()
        upd.mail_list.append(_msg(mail))
        try:
            asyncio.get_running_loop().create_task(client.send_packet(MSG_UPDATE, upd.SerializeToString()))
        except RuntimeError:
            pass


def _find(gid: int, acc, inst: int):
    """('own', mail dict) or ('captured', Mail) for an id in this player's box."""
    for m in _box(gid)["mails"]:
        if m["id"] == inst:
            return "own", m
    for m in _captured_mails(acc):
        if m.inst_id == inst:
            return "captured", m
    return None, None


def _state(gid: int, acc, kind, m) -> int:
    if kind == "own":
        return m["state"]
    return _box(gid)["captured"].get(str(m.inst_id), m.state)


def _set_state(gid: int, kind, m, state):
    if kind == "own":
        m["state"] = state
    else:
        _box(gid)["captured"][str(m.inst_id)] = state


def _items(kind, m) -> list[tuple[int, int]]:
    return [tuple(i) for i in m["items"]] if kind == "own" else [(i.conf_id, i.number) for i in m.item]


# --- handlers ----------------------------------------------------------------------------------
def on_login(client) -> list[tuple[int, bytes]]:
    ONLINE[int(client.gid)] = client
    out = P.MailInfoS2C()
    out.mail_list.extend(mail_list(client.gid, client.account))
    pvp.save()
    return [(MSG_INFO, out.SerializeToString())]


async def handle_read(client, pb: bytes):
    inst = P.MailReadC2S.FromString(pb).inst_id
    kind, m = _find(client.gid, client.account, inst)
    if kind and _state(client.gid, client.account, kind, m) == P.MAIL_UNREAD:
        _set_state(client.gid, kind, m, P.MAIL_READED_UNGET if _items(kind, m) else P.MAIL_DONE)
        pvp.save()
    await client.send_packet(MSG_READ, P.MailReadS2C(inst_id=inst, error=0 if kind else 1).SerializeToString())


async def handle_get(client, pb: bytes):
    req = P.MailGetItemC2S.FromString(pb)
    gid, acc = client.gid, client.account
    targets = []
    if req.get_all:
        for m in mail_list(gid, acc):
            kind, ref = _find(gid, acc, m.inst_id)
            if kind and _items(kind, ref) and _state(gid, acc, kind, ref) != P.MAIL_DONE:
                targets.append((kind, ref))
    else:
        kind, ref = _find(gid, acc, req.inst_id)
        if kind and _items(kind, ref) and _state(gid, acc, kind, ref) != P.MAIL_DONE:
            targets.append((kind, ref))
    rewards, ids = [], []
    for kind, ref in targets:
        rewards += _items(kind, ref)
        _set_state(gid, kind, ref, P.MAIL_DONE)
        ids.append(ref["id"] if kind == "own" else ref.inst_id)
    pvp.save()
    out = P.MailGetItemS2C(error=0 if ids else 1)
    out.inst_ids.extend(ids)
    await client.send_packet(MSG_GET, out.SerializeToString())
    if rewards:
        await pvp.HOOKS["grant_rewards"](client, rewards, True)


async def handle_remove(client, pb: bytes):
    req = P.MailRemoveC2S.FromString(pb)
    gid, acc = client.gid, client.account
    ids = [m.inst_id for m in mail_list(gid, acc)] if req.remove_all else [req.inst_id]
    removed = []
    for inst in ids:
        kind, ref = _find(gid, acc, inst)
        if not kind:
            continue
        if _items(kind, ref) and _state(gid, acc, kind, ref) != P.MAIL_DONE:
            continue  # attachments not taken yet: keep it
        if kind == "own":
            _box(gid)["mails"].remove(ref)
        else:
            _box(gid)["captured"][str(inst)] = "removed"
        removed.append(inst)
    pvp.save()
    out = P.MailRemoveS2C(error=0 if removed or req.remove_all else 1)
    out.inst_ids.extend(removed)
    await client.send_packet(MSG_REMOVE, out.SerializeToString())


HANDLERS = {MSG_READ: handle_read, MSG_GET: handle_get, MSG_REMOVE: handle_remove}
