"""
Locking / unlocking a player card (0x0909 PlayerCardLockC2S{inst_id, lock}). Before, the request was answered with
the captured empty reply and the card's lock never changed (it came back unlocked after the next login).

Evidenced (decoded_tshark.json, card 5203 unlocked and locked again): Garena sends 0x0902 PlayerCardChange with the
whole card, PlayerCard.locked (field 10) set as requested and no opt field, then the empty 0x0909 reply.
Not built: the TaskChange with only task id 7011101 (an SP mission of card 5203) that came right before both card
changes; why Garena re-sends it is not evidenced.
Assumed: an unknown card is answered with error 1 and nothing else.
"""
import wire
from accounts import card_info

MSG_LOCK, MSG_CARD_CHANGE = 0x0909, 0x0902
LOCKED = 10  # PlayerCard.locked


async def handle_lock(client, pb: bytes):
    f = wire.parse(pb)
    inst, lock = wire.get(f, 1, 0), bool(wire.get(f, 2, 0))
    acc = client.account
    card = acc.get_card(inst) if inst else None
    if card is None:
        await client.send_packet(MSG_LOCK, wire.build([(1, 0, 1)]))
        return
    fields = [x for x in wire.parse(card) if x[0] != LOCKED]
    if lock:
        fields.append((LOCKED, 0, 1))
    card = wire.build(sorted(fields, key=lambda x: x[0]))
    acc.put_card(card)
    acc.save()
    await client.send_packet(MSG_CARD_CHANGE, wire.build([(1, 2, card_info(card))]))  # captured: no opt field
    await client.send_packet(MSG_LOCK, b"")


HANDLERS = {MSG_LOCK: handle_lock}
