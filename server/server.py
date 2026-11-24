"""
Haikyuu Fly High - Private Server
TCP server on port 10104, handles seed exchange, encryption, and protobuf messages.
"""
import asyncio
import gzip
import struct
import os
import logging
import json
import time
from pathlib import Path

import gameconfig
import matches
import missions
import wire
from missions import Missions
import starter
from accounts import (
    CAPTURED,
    Account, MSG_CARD_CHANGE, MSG_CURRENCY_CHANGE, MSG_ITEM_CHANGE, MSG_LOTTERY_UPDATE, MSG_SIGN_INFO,
    MSG_TASK_CHANGE, STAMINA, TASK_FINISH,
    card_info, random_card_ids,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "server.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("hfh-server")

PORT = int(os.environ.get("HFH_PORT", "10104"))  # HFH_PORT: a second test server next to the main one
TIME_OFFSET = int(os.environ.get("HFH_TIME_OFFSET", "0"))  # test only: shift the clock the client sees
HEADER_SIZE = 8
FLAG_ENCRYPT = 0x01
FLAG_COMPRESS = 0x02
LCG_A = 214003
LCG_B = 2531012
LCG_MOD = 2**32

# Load message ID mappings
BASE = Path(__file__).parent.parent / "decoded"
MSG_ID_MAP = {}
try:
    for entry in json.load(open(BASE / "message_ids.json")):
        mid = int(entry["msg_id"], 16)
        MSG_ID_MAP[mid] = {
            "c2s": entry.get("c2s_type") or "",
            "s2c": entry.get("s2c_type") or "",
        }
except Exception as e:
    log.warning(f"Could not load message_ids.json: {e}")


def lcg_next(seed: int) -> tuple[int, bytes]:
    new = (seed * LCG_A + LCG_B) % LCG_MOD
    return new, struct.pack("<I", new)


def encrypt_data(data: bytes, seed_bytes: bytes) -> bytes:
    result = bytearray(len(data))
    prev = 0
    for i in range(len(data)):
        xored = data[i] ^ seed_bytes[i % 4]
        result[i] = (xored + prev) & 0xFF
        prev = result[i]
    return bytes(result)


def decrypt_data(data: bytes, seed_bytes: bytes) -> bytes:
    result = bytearray(len(data))
    prev = 0
    for i in range(len(data)):
        temp = (data[i] - prev) & 0xFF
        result[i] = seed_bytes[i % 4] ^ temp
        prev = data[i]
    return bytes(result)


def calculate_bcc(data: bytes) -> int:
    bcc = 0
    for b in data:
        bcc ^= b
    return bcc


def encode_varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def read_varint(data: bytes, offset: int) -> tuple[int | None, int]:
    result = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, offset
        shift += 7
        if shift > 63:
            return None, offset
    return None, offset


def decode_protobuf(data: bytes) -> dict:
    fields = {}
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        if tag is None:
            break
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num == 0 or field_num > 10000:
            break
        if wire_type == 0:
            val, offset = read_varint(data, offset)
            if val is None:
                break
            fields[field_num] = ("varint", val)
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            if length is None or offset + length > len(data):
                break
            fields[field_num] = ("bytes", data[offset:offset + length])
            offset += length
        elif wire_type == 1:
            if offset + 8 > len(data):
                break
            fields[field_num] = ("fixed64", struct.unpack_from("<Q", data, offset)[0])
            offset += 8
        elif wire_type == 5:
            if offset + 4 > len(data):
                break
            fields[field_num] = ("fixed32", struct.unpack_from("<I", data, offset)[0])
            offset += 4
        else:
            break
    return fields


def encode_protobuf(fields: dict) -> bytes:
    """Encode {field_num: (wire_type_name, value)} to protobuf bytes."""
    result = bytearray()
    for fnum in sorted(fields.keys()):
        wtype, val = fields[fnum]
        if wtype == "varint":
            result += encode_varint((fnum << 3) | 0)
            result += encode_varint(val)
        elif wtype == "bytes":
            if isinstance(val, str):
                val = val.encode("utf-8")
            result += encode_varint((fnum << 3) | 2)
            result += encode_varint(len(val))
            result += val
        elif wtype == "string":
            val = val.encode("utf-8")
            result += encode_varint((fnum << 3) | 2)
            result += encode_varint(len(val))
            result += val
        elif wtype == "fixed64":
            result += encode_varint((fnum << 3) | 1)
            result += struct.pack("<Q", val)
        elif wtype == "fixed32":
            result += encode_varint((fnum << 3) | 5)
            result += struct.pack("<I", val)
        elif wtype == "message":
            sub = encode_protobuf(val)
            result += encode_varint((fnum << 3) | 2)
            result += encode_varint(len(sub))
            result += sub
    return bytes(result)


class GameClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.addr = writer.get_extra_info("peername")
        self.encrypt_seed = int.from_bytes(os.urandom(4), "big")
        self.decrypt_seed = int.from_bytes(os.urandom(4), "big")
        self.c2s_seed = self.encrypt_seed
        self.s2c_seed = self.decrypt_seed
        self.gid = 0
        self.logged_in = False

    async def send_packet(self, msg_id: int, payload: bytes, encrypted: bool = True, compress: bool = False):
        command = (msg_id >> 8) & 0xFF
        action = msg_id & 0xFF
        body = b"\x00" + payload
        flags = FLAG_ENCRYPT if encrypted else 0x00
        if compress:  # gzip of the whole body, as the real server sent large match data
            body = gzip.compress(body)
            flags |= FLAG_COMPRESS

        if encrypted:
            self.s2c_seed, seed_bytes = lcg_next(self.s2c_seed)
            bcc = calculate_bcc(body)
            body = encrypt_data(body, seed_bytes)
        else:
            bcc = calculate_bcc(body)

        header = struct.pack("<I", len(body)) + bytes([command, action, flags, bcc])
        self.writer.write(header + body)
        await self.writer.drain()
        log.info(f"  -> {self.addr} 0x{msg_id:04X} ({len(body)} bytes)")

    async def send_raw(self, msg_id: int, flags: int, body: bytes):
        """Send a captured body as-is (already includes the leading 0x00, may be compressed)."""
        bcc = calculate_bcc(body)
        if flags & FLAG_ENCRYPT:
            self.s2c_seed, seed_bytes = lcg_next(self.s2c_seed)
            body = encrypt_data(body, seed_bytes)
        header = struct.pack("<I", len(body)) + bytes([(msg_id >> 8) & 0xFF, msg_id & 0xFF, flags, bcc])
        self.writer.write(header + body)
        await self.writer.drain()

    async def send_seed(self):
        body = b"\x00" + struct.pack(">I", self.encrypt_seed) + struct.pack(">I", self.decrypt_seed)
        header = struct.pack("<I", len(body)) + bytes([0x00, 0x00, 0x00, 0x00])
        self.writer.write(header + body)
        await self.writer.drain()
        log.info(f"  -> {self.addr} SEED enc=0x{self.encrypt_seed:08X} dec=0x{self.decrypt_seed:08X}")

    async def handle(self):
        log.info(f"Client connected: {self.addr}")
        await self.send_seed()

        buf = bytearray()
        try:
            while True:
                data = await self.reader.read(8192)
                if not data:
                    break
                buf.extend(data)

                while len(buf) >= HEADER_SIZE:
                    data_size = struct.unpack_from("<I", buf, 0)[0]
                    total = HEADER_SIZE + data_size
                    if len(buf) < total:
                        break

                    command = buf[4]
                    action = buf[5]
                    flags = buf[6]
                    bcc = buf[7]
                    msg_id = (command << 8) | action
                    body = bytes(buf[HEADER_SIZE:total])
                    del buf[:total]

                    if flags & FLAG_ENCRYPT:
                        self.c2s_seed, seed_bytes = lcg_next(self.c2s_seed)
                        body = decrypt_data(body, seed_bytes)
                        bcc_valid = calculate_bcc(body) == bcc
                        if not bcc_valid:
                            log.warning(f"  BCC mismatch for 0x{msg_id:04X}")

                    if body and body[0] == 0x00:
                        pb_data = body[1:]
                    else:
                        pb_data = body

                    log.info(f"  <- {self.addr} 0x{msg_id:04X} ({len(body)} bytes)")
                    await self.handle_message(msg_id, pb_data)

        except asyncio.IncompleteReadError:
            pass
        except ConnectionResetError:
            pass
        except Exception as e:
            log.error(f"Error handling {self.addr}: {e}")
        finally:
            log.info(f"Client disconnected: {self.addr}")
            if getattr(self, "gid", None) is not None and mail.ONLINE.get(int(self.gid)) is self:
                del mail.ONLINE[int(self.gid)]  # no more pushes (mail, chat) to a closed connection
                chat.on_logout(int(self.gid))  # "offline since" in the contact list
            self.writer.close()

    async def handle_message(self, msg_id: int, pb_data: bytes):
        handler = MESSAGE_HANDLERS.get(msg_id)
        if handler:
            await handler(self, pb_data)
        else:
            info = MSG_ID_MAP.get(msg_id, {})
            c2s = info.get("c2s", "")
            log.info(f"  Request 0x{msg_id:04X} ({c2s})")
            await handle_generic(self, pb_data, msg_id)


# --- Message Handlers ---

async def handle_login(client: GameClient, pb_data: bytes):
    """Handle 0x0101 LoginBySessionC2S → respond with LoginBySessionS2C."""
    fields = decode_protobuf(pb_data)
    gid = fields.get(1, ("varint", 0))[1]
    token = fields.get(7, ("bytes", b""))[1]
    if isinstance(token, bytes):
        try:
            token = token.decode("utf-8")
        except Exception:
            token = token.hex()

    log.info(f"  LOGIN gid={gid} token={token[:16]}...")
    import bindings  # shared server: the account belongs to the Garena login that used it first
    if not bindings.check(gid, token):
        log.warning(f"  LOGIN gid={gid} refused: token differs from the bound one (approve with "
                    f"/rebind or 'python bindings.py approve {gid}')")
        await client.send_packet(0x0101, encode_protobuf({1: ("varint", 1038)}))  # LOGIN_AUTH_FAILED
        return
    client.gid = gid
    client.logged_in = True

    session_id = os.urandom(16).hex()
    # the captured account for owner gids, a generated starter set for everyone else
    client.account = Account(gid, starter.baseline_for(gid))

    # LoginBySessionS2C:
    # 1=ERROR (0=SUCCESS), 2=Gamer, 7=time_zone, 9=index
    # Gamer: 1=account, 2=id, 3=level, 4=exp, 5=state, 8=session
    gamer = encode_protobuf({
        1: ("string", str(gid)),
        2: ("varint", gid),
        3: ("varint", client.account.level),
        4: ("varint", client.account.exp),
        5: ("varint", 0),
        8: ("string", session_id),
        9: ("varint", 0),
    })
    response = encode_protobuf({
        1: ("varint", 0),  # ERROR.SUCCESS
        2: ("bytes", gamer),  # Gamer main
        7: ("varint", 1),  # time_zone (UTC+1 for EU)
        9: ("varint", 0),  # index
        10: ("varint", 1),  # max_processe_num
        11: ("varint", 3),  # req_interval_sec
        12: ("varint", 0),  # server_type
    })
    await client.send_packet(0x0101, response)

    for m in client.account.b.sync:
        if m["msg_id"].upper() == "0X0B01" and client.account.team:  # line-ups saved since the baseline
            await client.send_packet(0x0B01, client.account.team)
            continue
        await client.send_raw(int(m["msg_id"], 16), m["flags"], bytes.fromhex(m["body_hex"]))
    kind = "captured" if client.account.b is CAPTURED else "starter"
    log.info(f"  SYNC sent {len(client.account.b.sync)} {kind} baseline messages, "
             f"level {client.account.level} exp {client.account.exp}")

    client.missions = Missions(client.account)
    if client.missions.check_reset():
        log.info(f"  MISSIONS reset to period {client.missions.period}")
    client.missions.save()
    client.account.save()
    for mid, pb in client.account.login_updates():
        await client.send_packet(mid, pb)
    await client.send_packet(MSG_TASK_CHANGE, client.missions.change_message(all_tasks=True))
    log.info(f"  ACCOUNT {gid}: {len(client.account.cards)} added cards, {len(client.account.items)} changed items")
    try:  # shared-server PvP: snapshot this player's team, arena info from this server's ladder
        for mid, pb in (pvp.on_login(client) + peak.on_login(client) + teamcomp.on_login(client)
                        + freedom.on_login(client) + mail.on_login(client) + personal.on_login(client)
                        + chat.on_login(client) + friends.on_login(client) + clubs.on_login(client)):
            await client.send_packet(mid, pb)
    except Exception as e:  # never block the login
        log.error(f"  PVP login update failed: {e!r}")

    # Send LoginSyncDataEndS2C (0x0102) - field 1=ERROR (0=SUCCESS)
    await client.send_packet(0x0102, encode_protobuf({1: ("varint", 0)}))

REPLIES = json.loads((Path(__file__).parent / "data" / "replies.json").read_text(encoding="utf-8"))


async def handle_heartbeat(client: GameClient, pb_data: bytes):
    """Handle 0x0301 PVPPingC2S → respond with PVPPingS2C."""
    import time
    fields = decode_protobuf(pb_data)
    client_time = fields.get(1, ("varint", 0))[1]

    # PVPPingS2C: 1=client_time (echo), 2=server_time
    response = encode_protobuf({
        1: ("varint", client_time),
        2: ("varint", int((time.time() + TIME_OFFSET) * 1000)),
    })
    await client.send_packet(0x0301, response)


RECRUIT_TICKET = 318


async def handle_lottery_do(client: GameClient, pb_data: bytes):
    """0x0F02 LotteryDoC2S (1=id, 2=count) -> new cards, cost, LotteryDoS2C."""
    req = wire.parse(pb_data)
    lottery_id, count = wire.get(req, 1, 0), wire.get(req, 2, 1) or 1
    acc = client.account
    cost_item, per_pull = gameconfig.lottery_cost(lottery_id) or (RECRUIT_TICKET, 1)
    is_currency = acc.is_currency(cost_item)
    have = acc.get_currency(cost_item) if is_currency else acc.item_count(cost_item)
    need = per_pull * count
    log.info(f"  RECRUIT lottery={lottery_id} count={count} cost={need}x{cost_item} have={have}")
    if have < need:
        await client.send_packet(0x0F02, wire.build([(1, 0, lottery_id)]))
        return

    infos, inst_ids = [], []
    for conf in acc.draw_cards(lottery_id, count):
        inst, card = acc.add_card(conf)
        inst_ids.append(inst)
        infos.append((1, 2, card_info(card)))
    if is_currency:
        cost_msg, cost_entry = MSG_CURRENCY_CHANGE, acc.set_currency(cost_item, have - need)
    else:
        cost_msg, cost_entry = MSG_ITEM_CHANGE, acc.set_item(cost_item, have - need)
    unit = acc.count_pulls(lottery_id, count)
    acc.save()

    await client.send_packet(MSG_CARD_CHANGE, wire.build(infos))
    await client.send_packet(cost_msg, wire.build([(1, 2, cost_entry)]))
    await client.send_packet(MSG_LOTTERY_UPDATE, wire.build([(1, 2, unit)]))
    if is_currency:
        await mission_event(client, missions.SPEND_CURRENCY, need, (cost_item,))
    packed = b"".join(wire.varint(i) for i in inst_ids)
    # LotteryDoS2C tags from its WriteTo in hotfix/Config.dll: 1=id 2=isPre 3=cardInstIds(packed) 4=rewards 5=err
    await client.send_packet(0x0F02, wire.build([(1, 0, lottery_id), (3, 2, packed)]))
    log.info(f"  RECRUIT gave {len(inst_ids)} cards, tickets left {have - count}")


MSG_NOTIFY_REWARDS = 0x0404  # NotifyRewardsS2C{1=repeated Item{2=conf_id 3=number}}: reward popup
MSG_LEVEL_CHANGE = 0x0406  # NotifyLevelChangeS2C{1=level 2=exp}: manager level


async def grant_rewards(client: GameClient, rewards: list[tuple[int, int]], popup: bool = True):
    """Credit rewards to the account, send the currency/item updates and (optionally) the reward popup."""
    acc = client.account
    updates, shown, levels = {}, [], []
    import clubs
    for conf_id, number in rewards:
        if clubs.credit(int(client.gid), conf_id, number):  # club points / funds / contribution
            shown.append((1, 2, wire.build([(2, 0, conf_id), (3, 0, number)])))
            continue
        if conf_id in gameconfig.MANAGER_EXP:
            levels += acc.add_player_exp(number)
            shown.append((1, 2, wire.build([(2, 0, conf_id), (3, 0, number)])))
            continue
        credited = acc.add_reward(conf_id, number)
        if credited is None:
            log.info(f"  reward {conf_id} x{number} not credited (unknown currency/item)")
            continue
        updates.setdefault(credited[0], []).append((1, 2, credited[1]))
        shown.append((1, 2, wire.build([(2, 0, conf_id), (3, 0, number)])))
    acc.save()
    for mid, entries in updates.items():
        await client.send_packet(mid, wire.build(entries))
    if popup and shown:
        await client.send_packet(MSG_NOTIFY_REWARDS, wire.build(shown))
    if any(c in gameconfig.MANAGER_EXP for c, _ in rewards):
        await client.send_packet(MSG_LEVEL_CHANGE, wire.build([(1, 0, acc.level), (2, 0, acc.exp)]))
    for level in levels:
        log.info(f"  LEVEL UP -> {level}")
        await grant_rewards(client, gameconfig.player_level_reward(level))


async def handle_sign_save(client: GameClient, pb_data: bytes):
    """0x0D02 SignSave: daily check-in -> SignSaveS2C{1=error 3=month_to_sign 4=sign_num}."""
    acc = client.account
    if acc.signed_today():
        s = acc.sign_state()
        log.info("  SIGN already checked in today")
        await client.send_packet(0x0D02, wire.build([(1, 0, 1), (3, 0, s["mask"]), (4, 0, s["num"])]))
        return
    s = acc.sign_in()
    now = time.localtime()
    rewards = gameconfig.daily_sign_rewards(now.tm_mon, now.tm_mday)
    log.info(f"  SIGN day {now.tm_mday}: total {s['num']}, rewards {rewards}")
    await grant_rewards(client, rewards)
    await client.send_packet(MSG_SIGN_INFO, wire.build([(1, 2, acc.sign_info())]))
    await client.send_packet(0x0D02, wire.build([(3, 0, s["mask"]), (4, 0, s["num"])]))


async def handle_sign_reward(client: GameClient, pb_data: bytes):
    """0x0D03 SignRewardC2S{1=sign_id}: claim a cumulative sign-in reward (totalsignin)."""
    sign_id = wire.get(wire.parse(pb_data), 1, 0)
    acc = client.account
    entry = gameconfig.total_sign_reward(sign_id)
    s = acc.sign_state()
    ok = entry is not None and s["num"] >= entry[0] and sign_id not in s["total"]
    log.info(f"  SIGN reward id={sign_id} ok={ok} entry={entry}")
    if not ok:
        await client.send_packet(0x0D03, wire.build([(1, 0, 1)]))
        return
    acc.claim_sign_total(sign_id)
    await grant_rewards(client, entry[1])
    await client.send_packet(MSG_SIGN_INFO, wire.build([(1, 2, acc.sign_info())]))
    await client.send_packet(0x0D03, b"")


async def mission_event(client: GameClient, code: int, amount: int = 1, params: tuple = ()):
    """Advance daily/weekly missions and push the change to the client."""
    m = client.missions
    m.event(code, amount, params)
    if m.changed_tasks:
        m.save()
        client.account.save()
        await client.send_packet(MSG_TASK_CHANGE, m.change_message())
        log.info(f"  MISSIONS progress code={code} +{amount} {params}")


async def claim_tasks(client: GameClient, task_ids: list[int]) -> list[int]:
    """Claim completed missions: credit taskdepotconfig rewards, book activity points, send TaskChange."""
    acc, m = client.account, client.missions
    claimed, rewards = [], []
    for tid in task_ids:
        if acc.task_state(tid) != TASK_FINISH:
            continue
        acc.mark_task_claimed(tid)
        m.changed_tasks.append(tid)
        rewards += m.on_claimed(tid, gameconfig.task_rewards(tid) or [])
        claimed.append(tid)
    if claimed:
        m.save()
        await grant_rewards(client, rewards)
        await client.send_packet(MSG_TASK_CHANGE, m.change_message())
    log.info(f"  TASK claimed {len(claimed)} of {len(task_ids)}, rewards {rewards[:6]}, activation {m.activation}")
    return claimed


CARD_UPDATE, CARD_REMOVE = 1, 2  # PlayerCardChangeS2C.opt (2): CREATE 0 / UPDATE 1 / REMOVE 2


async def _card_error(client, msg_id, inst, reason):
    log.info(f"  CARD 0x{msg_id:04X} inst={inst}: {reason}")
    await client.send_packet(msg_id, wire.build([(1, 0, inst), (2, 0, 1)]))


async def handle_card_level_up(client: GameClient, pb_data: bytes):
    """0x0905 PlayerCardLevelUpC2S{1=inst_id 2=optype}: spend EXP (102) to raise the card level."""
    req = wire.parse(pb_data)
    inst, optype = wire.get(req, 1, 0), wire.get(req, 2, 0)
    acc = client.account
    card = acc.get_card(inst)
    if card is None:
        return await _card_error(client, 0x0905, inst, "unknown card")
    f = wire.parse(card)
    conf, level, star = wire.get(f, 3), wire.get(f, 4, 1), wire.get(f, 6, 0)
    cap = gameconfig.level_cap(star)
    exp = acc.get_currency(gameconfig.CARD_EXP)
    steps = 0
    for want in range(1, (1 if optype == 0 else 3) + 1):  # UI buttons: optype 0 = "+1", otherwise "+3"
        cost = gameconfig.level_up_cost(conf, level, want)
        if level + want > cap or cost is None or cost > exp:
            break
        steps = want
    log.info(f"  CARD level-up inst={inst} conf={conf} lv={level} star={star} cap={cap} optype={optype} steps={steps}")
    if steps == 0:
        return await _card_error(client, 0x0905, inst, "at level cap or not enough EXP")
    cost = gameconfig.level_up_cost(conf, level, steps)
    f = [x for x in wire.set_varint(f, 4, level + steps) if x[0] != 5]  # exp resets with each level
    card = wire.build(f)
    acc.put_card(card)
    exp_entry = acc.set_currency(gameconfig.CARD_EXP, exp - cost)
    acc.save()
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, exp_entry)]))
    await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(card)), (2, 0, CARD_UPDATE)]))
    await client.send_packet(0x0905, wire.build([(1, 0, inst)]))
    await mission_event(client, missions.LEVEL_UP_CARD, steps)


async def handle_card_train(client: GameClient, pb_data: bytes):
    """0x0908 PlayerCardTrainC2S{1=inst_id}: next training step (train table: items + gold)."""
    inst = wire.get(wire.parse(pb_data), 1, 0)
    acc = client.account
    card = acc.get_card(inst)
    if card is None:
        return await _card_error(client, 0x0908, inst, "unknown card")
    f = wire.parse(card)
    conf, train = wire.get(f, 3), wire.get(f, 7, 0)
    step = gameconfig.train_step(conf, train + 1)
    if step is None:
        return await _card_error(client, 0x0908, inst, f"no training step {train + 1}")
    items, gold = step
    lacking = [(i, n) for i, n in items if acc.item_count(i) < n]
    if lacking or acc.get_currency(gameconfig.GOLD) < gold:
        return await _card_error(client, 0x0908, inst, f"missing {lacking} or gold {gold}")
    item_entries = [(1, 2, acc.set_item(i, acc.item_count(i) - n)) for i, n in items]
    gold_entry = acc.set_currency(gameconfig.GOLD, acc.get_currency(gameconfig.GOLD) - gold)
    card = wire.build(wire.set_varint(f, 7, train + 1))
    acc.put_card(card)
    acc.save()
    log.info(f"  CARD train inst={inst} conf={conf} step {train + 1}: -{items} -{gold} gold")
    await client.send_packet(MSG_ITEM_CHANGE, wire.build(item_entries))
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, gold_entry)]))
    await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(card)), (2, 0, CARD_UPDATE)]))
    await client.send_packet(0x0908, wire.build([(1, 0, inst)]))
    await mission_event(client, missions.TRAIN_CARD, 1)


async def handle_card_star(client: GameClient, pb_data: bytes):
    """0x0906 PlayerCardUpGradeStarC2S{1=inst 2=type 3=consume_inst_ids(packed) 4=cost_money 5=cost_player_exp}.

    Next star costs upgradestar[star+1].Gold and .Amount material cards of at least the same star;
    the card must be at its current level cap.
    """
    req = wire.parse(pb_data)
    inst = wire.get(req, 1, 0)
    consume = []
    for num, wt, v in req:
        if num == 3:
            consume += _packed(v) if wt == 2 else [v]
    acc = client.account
    card = acc.get_card(inst)
    if card is None:
        return await _card_error(client, 0x0906, inst, "unknown card")
    f = wire.parse(card)
    level, star = wire.get(f, 4, 1), wire.get(f, 6, 0)
    row = gameconfig.STAR_LIMITS.get(star + 1) if gameconfig.AVAILABLE else None
    if row is None:
        return await _card_error(client, 0x0906, inst, f"no star {star + 1}")
    gold, amount = row.get("Gold") or 0, row.get("Amount") or 0
    mats = [acc.get_card(i) for i in consume if i != inst]
    if (level < gameconfig.level_cap(star) or len(mats) != amount or None in mats
            or any(wire.get(wire.parse(m), 6, 0) < star for m in mats)
            or acc.get_currency(gameconfig.GOLD) < gold):
        return await _card_error(client, 0x0906, inst, f"requirements not met (lv {level}, star {star}, mats {len(mats)}/{amount})")
    removed = [acc.remove_card(i) for i in consume if i != inst]
    card = wire.build(wire.set_varint(f, 6, star + 1))
    acc.put_card(card)
    gold_entry = acc.set_currency(gameconfig.GOLD, acc.get_currency(gameconfig.GOLD) - gold)
    acc.save()
    log.info(f"  CARD star inst={inst} {star} -> {star + 1}, -{gold} gold, consumed {len(removed)}")
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, gold_entry)]))
    await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(c)) for c in removed] + [(2, 0, CARD_REMOVE)]))
    await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(card)), (2, 0, CARD_UPDATE)]))
    await client.send_packet(0x0906, wire.build([(1, 0, inst)]))
    await mission_event(client, missions.PROMOTE_CARD, 1)


async def handle_card_skill(client: GameClient, pb_data: bytes):
    """0x0907 PlayerCardUpGradeSkillC2S{1=inst 2=consume_inst_ids 3=consume_item_inst_ids} (packed).

    Each material (same-name card or skill book) costs playerlist.Skillgold and raises one random
    upgradable skill by a level (skilllist.Skilllevelweight).
    """
    req = wire.parse(pb_data)
    inst = wire.get(req, 1, 0)
    cards, books = [], []
    for num, wt, v in req:
        if num in (2, 3):
            (cards if num == 2 else books).extend(_packed(v) if wt == 2 else [v])
    acc = client.account
    card = acc.get_card(inst)
    if card is None:
        return await _card_error(client, 0x0907, inst, "unknown card")
    f = wire.parse(card)
    conf = wire.get(f, 3)
    skills = _packed(wire.get(f, 8, b""))
    book_items = [acc.item_by_inst(b) for b in books]
    mats = [acc.get_card(i) for i in cards if i != inst]
    gold = gameconfig.skill_gold(conf) * (len(mats) + len(books))
    if (not mats and not books) or None in mats or None in book_items \
            or any(acc.item_count(i) < book_items.count(i) for i in set(book_items)) \
            or acc.get_currency(gameconfig.GOLD) < gold:
        return await _card_error(client, 0x0907, inst, f"requirements not met (cards {cards}, books {books}, gold {gold})")
    ups = []
    for _ in range(len(mats) + len(books)):
        i = gameconfig.roll_skill_upgrade(skills)
        if i is None:
            break
        skills[i] += 1
        ups.append(skills[i])
    removed = [acc.remove_card(i) for i in cards if i != inst]
    item_entries = [(1, 2, acc.set_item(i, acc.item_count(i) - book_items.count(i))) for i in set(book_items)]
    card = wire.build(wire.replace(f, 8, b"".join(wire.varint(s) for s in skills)))
    acc.put_card(card)
    gold_entry = acc.set_currency(gameconfig.GOLD, acc.get_currency(gameconfig.GOLD) - gold)
    acc.save()
    log.info(f"  CARD skill inst={inst} conf={conf}: upgraded {ups}, -{gold} gold, cards {len(removed)}, books {len(books)}")
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, gold_entry)]))
    if item_entries:
        await client.send_packet(MSG_ITEM_CHANGE, wire.build(item_entries))
    if removed:
        await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(c)) for c in removed] + [(2, 0, CARD_REMOVE)]))
    await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(card)), (2, 0, CARD_UPDATE)]))
    await client.send_packet(0x0907, wire.build([(1, 0, inst)]))
    await mission_event(client, missions.UPGRADE_SKILL, len(ups))


async def handle_activation_chest(client: GameClient, pb_data: bytes):
    """0x0E04 TaskReceiveActivationRewardsC2S{1=activation_id}: open a daily/weekly activity chest."""
    chest = wire.get(wire.parse(pb_data), 1, 0)
    m = client.missions
    rewards = m.claim_chest(chest)
    log.info(f"  CHEST {chest} rewards={rewards}")
    if rewards is None:
        await client.send_packet(0x0E04, wire.build([(1, 0, 1)]))
        return
    m.save()
    await grant_rewards(client, rewards)
    await client.send_packet(MSG_TASK_CHANGE, m.change_message())
    await client.send_packet(0x0E04, b"")


async def handle_task_receive(client: GameClient, pb_data: bytes):
    """0x0E03 TaskReceiveRewardsC2S{1=task_id} -> S2C{1=error 2=task_id}."""
    tid = wire.get(wire.parse(pb_data), 1, 0)
    ok = await claim_tasks(client, [tid])
    await client.send_packet(0x0E03, wire.build([(2, 0, tid)] if ok else [(1, 0, 1), (2, 0, tid)]))


async def handle_task_one_key(client: GameClient, pb_data: bytes):
    """0x0E05 TaskReceiveOneKeyC2S{1=receives (packed task ids)}: claim several; empty = all completed."""
    ids = []
    for num, wt, v in wire.parse(pb_data):
        if num == 1:
            ids += _packed(v) if wt == 2 else [v]
    await claim_tasks(client, ids or client.account.finished_tasks())
    await client.send_packet(0x0E05, b"")


async def handle_lottery_gift(client: GameClient, pb_data: bytes):
    """0x0F04 LotteryGetGiftC2S{1=id, 2=count}: claim cumulative-pull milestones (count -1 = all reached)."""
    req = wire.parse(pb_data)
    lottery_id, count = wire.get(req, 1, 0), wire.get(req, 2, 0)
    acc = client.account
    table = gameconfig.milestones(lottery_id)
    reached = acc.all_count(lottery_id)
    claimable = [c for c in table if c <= reached and c not in acc.claimed_milestones(lottery_id)]
    if count < 1 << 63:  # a single milestone, not -1
        claimable = [c for c in claimable if c == count]
    log.info(f"  MILESTONE lottery={lottery_id} count={count} claim={claimable}")
    if not claimable:
        await client.send_packet(0x0F04, wire.build([(1, 0, lottery_id), (2, 0, count), (3, 0, 1)]))
        return
    rewards = []
    for c in claimable:
        total = acc.claim_milestone(lottery_id, c)
        rewards += table[c]
    await grant_rewards(client, rewards, popup=False)
    await client.send_packet(MSG_LOTTERY_UPDATE, wire.build([(4, 2, total)]))
    await client.send_packet(0x0F04, wire.build([(1, 0, lottery_id), (2, 0, count)]))


PVP_TYPE_PRACTICE = 3  # FIGHT_SOURCE
FIGHT_PEAK = 15
PRACTICE_STAMINA_COST = 15


async def handle_pvp_create(client: GameClient, pb_data: bytes):
    """0x0302 PVPCreateC2S{1=args PVPRoomArgs{1=pvp_type, 2=pvp_args (packed)}}: stage matches cost stamina."""
    args = wire.parse(wire.get(wire.parse(pb_data), 1, b""))
    pvp_type = wire.get(args, 1, 0)
    pvp_args = _packed(wire.get(args, 2, b""))
    stage = pvp_args[1] if len(pvp_args) > 1 else 0
    cost = gameconfig.stage_cost(stage)
    if cost is None and pvp_type == PVP_TYPE_PRACTICE:
        cost = (STAMINA, PRACTICE_STAMINA_COST)
    client.last_pvp = (pvp_type, stage)
    if cost and cost[0] == STAMINA:
        acc = client.account
        entry = acc.spend_stamina(cost[1])
        acc.save()
        await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, entry)]))
        log.info(f"  MATCH type={pvp_type} stage={stage}: -{cost[1]} stamina, now {acc.get_currency(STAMINA)}")
        await mission_event(client, missions.CONSUME_CURRENCY, cost[1], (STAMINA,))
    client.room = None
    args_pb = wire.get(wire.parse(pb_data), 1, b"")
    room = (pvp.build_room(args_pb, client.account, client.gid, player_name(client))
            or peak.build_room(args_pb, client.account, client.gid, player_name(client))
            or teamcomp.build_room(args_pb, client.account, client.gid, player_name(client))
            or freedom.build_room(args_pb, client.account, client.gid, player_name(client))
            or matches.build_room(args_pb, client.account, client.gid, player_name(client)))
    if room:
        client.room = room[1]
        m = room[1]
        outcome = f"live, home point chance {m.live.win_chance:.2f}" if m.live else ('win' if m.won else 'loss')
        log.info(f"  ROOM stage={stage} preset={m.info.preset_match_id} power {m.info.home.power} vs "
                 f"{m.info.away.power} -> {outcome} stars={m.stars:03b}")
        await client.send_packet(0x0302, room[0])
        return
    await handle_generic(client, pb_data, 0x0302)


def _packed(data: bytes) -> list[int]:
    vals, pos = [], 0
    while pos < len(data):
        v, pos = wire.read_varint(data, pos)
        vals.append(v)
    return vals


def player_name(client: GameClient) -> str:
    """Name from the account's PersonalInfoS2C (field 3)."""
    name = wire.get(wire.parse(client.account.b.pb("0x2101")), 3, b"")
    return name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)


async def handle_preset_result(client: GameClient, match):
    """Result of a server-built match: decided outcome, stage rewards, stage progress and stars."""
    acc = client.account
    room, won, stars = match.info, match.won, match.stars
    stage = matches.stage_of(room.room_args)
    first, updates, rewards = False, [], []
    if won:
        first = not acc.stage_stars().get(stage)  # cleared before (baseline or this account) = no first reward
        rewards = matches.roll_stage_rewards(stage, first)
        updates = acc.clear_stage(room.room_args.pvp_type, stage, stars)[1]
        if room.room_args.pvp_type == matches.P.FIGHT_CONTEST:  # Extreme Training: next floor opens
            updates += acc.clear_contest(stage - matches.CONTEST_STAGE_BASE)
    await client.send_packet(0x0304, matches.build_result(room, won, stars, rewards))
    await grant_rewards(client, rewards, popup=False)
    for mid, pb in updates:
        await client.send_packet(mid, pb)
    log.info(f"  STAGE {stage} {'won' if won else 'lost'} stars={stars:03b} first={first} rewards={rewards}")
    await mission_event(client, missions.FINISH_MATCH, 1, (room.room_args.pvp_type, stage))


async def handle_tokyo_reward(client: GameClient, pb_data: bytes):
    """0x1103 FightTokyoRewardC2S{1=chapter}: open every reached, unclaimed star chest of the chapter."""
    chapter = wire.get(wire.parse(pb_data), 1, 0)
    acc = client.account
    stages, chests = gameconfig.chapter_chests(chapter)
    stars_by_stage = acc.stage_stars()
    have = sum(bin(stars_by_stage.get(s, 0)).count("1") for s in stages)
    claimed = acc.tokyo_claimed(chapter)
    ready = [need for need in sorted(chests) if need <= have and need not in claimed]
    log.info(f"  CHAPTER {chapter} stars={have} claim={ready}")
    if not ready:
        await client.send_packet(0x1103, wire.build([(1, 0, 1)]))
        return
    rewards = []
    for need in ready:
        acc.tokyo_rewards.append([chapter, need])
        rewards += gameconfig.open_rewards(chests[need])
    await grant_rewards(client, rewards)
    await client.send_packet(0x1103, wire.build(
        [(2, 2, wire.build([(1, 0, chapter), (2, 0, need)])) for need in ready]))


LIVE_REPLIES = {0x0307: "PVPHandleServeS2C", 0x030A: "PVPUltimateSkillS2C", 0x030B: "PVPMoraleSkillS2C"}


def _live(client: GameClient):
    room = getattr(client, "room", None)
    return room.live if room is not None and room.live else None


async def handle_match_data(client: GameClient, pb_data: bytes):
    """0x0303 PVPMatchDataC2S: next rally of a live match."""
    live = _live(client)
    if live and live.rally_no == 0 and client.account.flags:  # auto play left on from the last match
        live.auto_skill = bool(client.account.flags & 1)
    if not live:
        await handle_generic(client, pb_data, 0x0303)
        return
    if live.over:
        log.info("  LIVE match already over, resending the last rally")
        await client.send_packet(0x0303, live.reply(live.last), compress=True)
        return
    frames = live.next_rally()
    log.info(f"  LIVE rally {live.rally_no}: {len(frames)} frames, score {live.score[0]}-{live.score[1]}"
             f"{' (match over)' if live.over else ''}")
    await client.send_packet(0x0303, live.reply(frames), compress=True)


async def handle_match_input(client: GameClient, pb_data: bytes, msg_id: int):
    """Hand serve / ultimate / morale skill: the rally is re-simulated; stage 1 keeps it unchanged."""
    live = _live(client)
    if not live or not live.last:
        await handle_generic(client, pb_data, msg_id)
        return
    try:
        frames = live.apply_input(msg_id, pb_data)
    except Exception as e:  # never leave the client waiting: fall back to the unchanged rally
        log.error(f"  LIVE input 0x{msg_id:04X} failed: {e!r}")
        frames = live.last
    msg = getattr(matches.P, LIVE_REPLIES[msg_id])(order=live.order, session=live.info.session)
    msg.frames.extend(frames)
    if msg_id == 0x030A:
        values = matches.P.PVPUltimateSkillC2S.FromString(pb_data).values
        if values:  # result is a single SkillInput: echo the first choice
            msg.result.player_id, msg.result.skill_id = values[0].player_id, values[0].skill_id
    live.order += 1
    if getattr(live, "input_note", None):
        log.info(f"  LIVE {live.input_note}")
        live.input_note = None
    log.info(f"  LIVE input 0x{msg_id:04X}: rally {live.rally_no} replayed, inputs {live.inputs}, "
             f"score {live.score[0]}-{live.score[1]}")
    await client.send_packet(msg_id, msg.SerializeToString(), compress=True)


async def handle_flag_set(client: GameClient, pb_data: bytes):
    """0x0203 MainFlagSetC2S{units FlagUnit{flag, open}}: client switches such as auto play, auto serve
    and match speed. The client takes its state from the reply, so echo what it asked for (the captured
    reply had them all off, which is why auto play would not switch on)."""
    P = matches.P
    req = P.MainFlagSetC2S.FromString(pb_data)
    rep = P.MainFlagSetS2C()
    acc = client.account
    for u in req.units:
        rep.units.add(flag=u.flag, open=u.open)
        acc.flags = ((acc.flags or 0) | (1 << u.flag)) if u.open else ((acc.flags or 0) & ~(1 << u.flag))
    acc.save()  # kept over restarts and sent again at login
    live = _live(client)
    if live:
        live.auto_skill = bool(acc.flags & 1)
    log.info(f"  FLAGS {[(P.FlagUnit.DESCRIPTOR.fields_by_name['flag'].enum_type.values_by_number[u.flag].name, u.open) for u in req.units]}")
    await client.send_packet(0x0203, rep.SerializeToString())


async def handle_match_setting(client: GameClient, pb_data: bytes):
    """0x030C PVPMatchSettingC2S: auto play in a match. With auto_release_driving_skill the server
    releases the user's ultimates itself from the next rally on (no ultimate windows)."""
    P = matches.P
    req = P.PVPMatchSettingC2S.FromString(pb_data)
    live = _live(client)
    rep = P.PVPMatchSettingS2C(auto_release_driving_skill=req.auto_release_driving_skill,
                               auto_hander_serve=req.auto_hander_serve)
    if live:
        live.auto_skill = req.auto_release_driving_skill
        rep.session = live.info.session
    log.info(f"  MATCH SETTING auto skill={req.auto_release_driving_skill} auto serve={req.auto_hander_serve}")
    await client.send_packet(0x030C, rep.SerializeToString())


async def handle_formation_change(client: GameClient, pb_data: bytes):
    """0x0309 PVPFormationChangeC2S: substitutions during a live match (next rally)."""
    live = _live(client)
    if not live:
        await handle_generic(client, pb_data, 0x0309)
        return
    try:
        reply = live.formation_change(pb_data)
    except Exception as e:
        log.error(f"  LIVE substitution failed: {e!r}")
        reply = matches.P.PVPFormationChangeS2C(session=live.info.session).SerializeToString()
    log.info(f"  LIVE substitution: pending {live.pending_subs}, left {live.subs_left}")
    await client.send_packet(0x0309, reply)


async def handle_pvp_result(client: GameClient, pb_data: bytes):
    """0x0304: built result for our own rooms; otherwise replay the captured result and credit its rewards."""
    room = getattr(client, "room", None)
    if room is not None:
        client.room = None
        if getattr(room, "pvp", None):  # arena: ladder score, records, arena coins
            change = await pvp.handle_result(client, room)
            log.info(f"  ARENA {'won' if room.won else 'lost'} vs {room.pvp.entry['gid']}: score {change:+d}")
            await mission_event(client, missions.FINISH_MATCH, 1, (room.info.room_args.pvp_type, 0))
            return
        if getattr(room, "freedom", None):  # Freedom Championship: ladder score for score matches
            h, a, change = await freedom.handle_result(client, room)
            log.info(f"  FREEDOM vs {room.freedom.entry['gid']} ({'score' if room.freedom.pattern else 'training'}): "
                     f"{h}-{a}, score {change:+d}")
            await mission_event(client, missions.FINISH_MATCH, 1, (room.info.room_args.pvp_type, 0))
            return
        if getattr(room, "streak", None):  # Match Streak: stage of a competition, stars at its end
            h, a, settled = await teamcomp.handle_result(client, room)
            log.info(f"  STREAK comp {room.streak.comp} stage {room.streak.index + 1}: {h}-{a}"
                     + (f", lead {settled['lead']} stars {settled['stars']}" if settled else ""))
            await mission_event(client, missions.FINISH_MATCH, 1, (room.info.room_args.pvp_type, room.streak.stage))
            return
        if getattr(room, "peak", None):  # Triple Clash: one game of the series, ladder at its end
            h, a, settled = await peak.handle_result(client, room)
            log.info(f"  PEAK game {room.peak.team + 1} vs {room.peak.entry['gid']}: {h}-{a}"
                     + (f", series {'won' if settled['won'] else 'lost'} {settled['change']:+d}" if settled else ""))
            if settled:
                await mission_event(client, missions.FINISH_MATCH, 1, (room.info.room_args.pvp_type, 0))
                await mission_event(client, missions.PEAK_MATCH, 1)
            return
        await handle_preset_result(client, room)
        return
    await handle_generic(client, pb_data, 0x0304)
    reply = REPLIES["0x0304"]
    result = wire.parse(wire.body_to_pb(bytes.fromhex(reply["body_hex"]), reply["flags"]))
    battle = wire.parse(wire.get(result, 2, b""))
    rewards = [(wire.get(wire.parse(raw), 2), wire.get(wire.parse(raw), 3, 0)) for num, _, raw in battle if num == 4]
    # the result screen shows the rewards itself; 113 (card EXP) is not modelled and gets skipped
    await grant_rewards(client, rewards, popup=False)
    log.info(f"  MATCH rewards {rewards}, manager level {client.account.level} exp {client.account.exp}")
    pvp_type, stage = getattr(client, "last_pvp", (0, 0))
    await mission_event(client, missions.FINISH_MATCH, 1, (pvp_type, stage))
    if pvp_type == FIGHT_PEAK:
        await mission_event(client, missions.PEAK_MATCH, 1)


async def handle_pause(client: GameClient, pb_data: bytes):
    """0x030F PVPPauseC2S{1=is_pause}: echo the asked state with this match's session (the captured
    reply always said "paused" for another session)."""
    is_pause = wire.get(wire.parse(pb_data), 1, 0)
    live = _live(client)
    room = getattr(client, "room", None)
    session = live.info.session if live else (room.info.session if room is not None else 0)
    await client.send_packet(0x030F, wire.build([(2, 0, session), (3, 0, is_pause)]))


async def handle_team_save(client: GameClient, pb_data: bytes):
    """0x0B02 TeamFormationSaveC2S{1=formation 2=module}: keeps the line-up (players, libero preset,
    auto switch); matches are built from it."""
    fields = wire.parse(pb_data)
    formation = wire.get(fields, 1, b"")
    if formation:
        client.account.save_formation(formation)
        f = matches.P.TeamFormation.FromString(formation)
        log.info(f"  TEAM line-up {f.lineup} saved: {len(f.inst_ids)} players, libero preset "
                 f"{f.libero_replace_type}, auto switch {f.auto_replace}")
    await client.send_packet(0x0B02, wire.build([(2, 0, wire.get(fields, 2, 0))]))


async def handle_generic(client: GameClient, pb_data: bytes, msg_id: int):
    """Reply with the captured real-server reply for this ID, else an empty one."""
    reply = REPLIES.get(f"0x{msg_id:04X}")
    if reply:
        await client.send_raw(msg_id, reply["flags"], bytes.fromhex(reply["body_hex"]))
        log.info(f"  Replayed captured reply 0x{msg_id:04X}")
        return
    log.info(f"  Auto-ACK 0x{msg_id:04X} (no captured reply)")
    await client.send_packet(msg_id, encode_protobuf({}))


MESSAGE_HANDLERS = {
    0x0101: handle_login,
    0x0301: handle_heartbeat,
    0x0F02: handle_lottery_do,
    0x0F04: handle_lottery_gift,
    0x0D02: handle_sign_save,
    0x0D03: handle_sign_reward,
    0x0E03: handle_task_receive,
    0x0E05: handle_task_one_key,
    0x0E04: handle_activation_chest,
    0x0905: handle_card_level_up,
    0x0908: handle_card_train,
    0x0906: handle_card_star,
    0x0907: handle_card_skill,
    0x0302: handle_pvp_create,
    0x0304: handle_pvp_result,
    0x0303: handle_match_data,
    0x0307: lambda c, pb: handle_match_input(c, pb, 0x0307),
    0x030A: lambda c, pb: handle_match_input(c, pb, 0x030A),
    0x030B: lambda c, pb: handle_match_input(c, pb, 0x030B),
    0x0309: handle_formation_change,
    0x030C: handle_match_setting,
    0x0203: handle_flag_set,
    0x0B02: handle_team_save,
    0x030F: handle_pause,
    0x1103: handle_tokyo_reward,
}

import admin  # noqa: E402  Admin Mode (gift code) and putting potentials on/off
MESSAGE_HANDLERS.update(admin.HANDLERS)

import pvp  # noqa: E402  asynchronous PvP (arena) against the other players on this server
MESSAGE_HANDLERS.update(pvp.HANDLERS)
import peak  # noqa: E402  Triple Clash (three teams, best of three) against the other players
MESSAGE_HANDLERS.update(peak.HANDLERS)
import teamcomp  # noqa: E402  Match Streak (TeamCompetition): 2-3 stages in a row, stars for the lead
MESSAGE_HANDLERS.update(teamcomp.HANDLERS)
import freedom  # noqa: E402  Freedom Championship: matchmaking against the other players' line-ups
MESSAGE_HANDLERS.update(freedom.HANDLERS)
import mail  # noqa: E402  mailbox (season / daily rank rewards)
import seasons  # noqa: E402  season ends and daily arena rewards, sent as mail
import personal  # noqa: E402  time-limited frames / chat bubbles / titles (season rewards)
MESSAGE_HANDLERS.update(mail.HANDLERS)
MESSAGE_HANDLERS.update(personal.HANDLERS)  # profile choices (title, frame, chat frame ...)
import chat  # noqa: E402  world chat and private messages between the players of this server
MESSAGE_HANDLERS.update(chat.HANDLERS)
import friends  # noqa: E402  friends list, requests, block list, friendship tokens
MESSAGE_HANDLERS.update(friends.HANDLERS)
import clubs  # noqa: E402  clubs (Association): create, join, roles, notices, club chat
MESSAGE_HANDLERS.update(clubs.HANDLERS)
pvp.HOOKS.update(grant_rewards=grant_rewards, player_name=player_name, generic=handle_generic,
                 mission_event=mission_event)


async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    client = GameClient(reader, writer)
    await client.handle()


async def main():
    server = await asyncio.start_server(on_client, "0.0.0.0", PORT)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info(f"Haikyuu Fly High Private Server running on {addrs}")
    if admin.multiplayer():
        log.info("Multiplayer server: admin rights only for the owner and granted admins")
    asyncio.get_running_loop().create_task(seasons.loop())  # daily / season-end reward mail
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
