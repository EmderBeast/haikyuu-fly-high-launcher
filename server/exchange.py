"""
Currency exchange (0x1201 CurrencyExchangeC2S {exchange_type, num}): buying stamina with diamonds (type 2) and
diamonds from paid diamonds (type 3), per player and day; also the assistant's function 5 ("Exchange stamina",
count = its arg).

Before, 0x1201 had no handler (only confirmed): buying stamina did nothing.

Evidenced:
  - exchange.json: type 2 costs by the exchange's number of the day (Limit [from, to]): 1-2 free, 3: 50,
    4-5: 75, 6-7: 100, 8: 150 diamonds (108), each 60 stamina (105); at most 8 (the highest Limit; exchangefunction ID 2 Value 8);
    type 3: 109 x1 -> 108 x1, no limit (Limit [1, 0]);
  - captures wcy2711 (n 371-374) and decrypted_1: the assistant's two exchanges of the day: per exchange
    0x0405 NotifyCurrencyChange {2 = CurrencyExchange {type 2, exchange_num 1 / 2}}, then 0x0405 with the new
    stamina (+60), no diamond change (both free); assistant unit 5 = (105, 60) twice;
  - the login's 0x0407 (CurrencySync) carries the day's counts as field 2 (6 captures: {type 2, num 2} after
    the assistant; none in wcy2711, the first login of a new day);
  - client: CurrencyMessageHandler.OnCurrencyExchange only checks err (and shows a tip for type 3), no pop-up.
Assumed: the day changes at 06:00 UTC (as the other daily resets); a paid exchange reports the diamond cost in
the same 0x0405 as the new count; a request that is over the limit or cannot be paid gets err FAILED and changes
nothing; type 1 (gold) is not in exchange.json and is refused.
State: pvp.json "exchange" {gid: {day, counts {type: n}}}.
"""
import time

import pvp
import onlinereward
import wire

MSG_EXCHANGE, MSG_SYNC = 0x1201, 0x0407


def _P():
    import matches
    return matches.P


def _rows() -> list[dict]:
    return list(pvp._cfg("exchange"))


def limit(kind: int) -> int:
    """Most exchanges of a type per day: the highest Limit of its exchange.json rows (type 2: 8, which is also
    exchangefunction ID 2); 0 = no limit (type 3: Limit [1, 0])."""
    his = [(r.get("Limit") or [1, 0])[-1] for r in _rows() if r.get("ExchangeType") == kind]
    return 0 if not his or 0 in his else max(his)


def price(kind: int, n: int):
    """(cost currency, cost, reward currency, reward) of the n-th exchange of the day, or None."""
    for r in _rows():
        if r.get("ExchangeType") != kind:
            continue
        lim = r.get("Limit") or [1, 0]
        lo, hi = lim[0], (lim[1] if len(lim) > 1 else 0)
        if lo <= n and (hi == 0 or n <= hi):
            (cc, cn), (rc, rn) = r["Consume"], r["Reward"]
            return cc, cn, rc, rn
    return None


def state(gid: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    st = pvp.state().setdefault("exchange", {})
    d = st.setdefault(str(gid), {"day": 0, "counts": {}})
    day = onlinereward.day_start(now)
    if d["day"] != day:
        d.update(day=day, counts={})
    return d


def patch_sync(gid: int, body: bytes) -> bytes:
    """The login's 0x0407 with this player's exchange counts of today (field 2) instead of the baseline's."""
    d = state(gid)
    fields = [f for f in wire.parse(body) if f[0] != 2]
    for kind, n in sorted(d["counts"].items(), key=lambda kv: int(kv[0])):
        if n:
            fields.append((2, 2, wire.build([(1, 0, int(kind)), (2, 0, n)])))
    return wire.build(fields)


async def exchange_once(client, kind: int) -> tuple[int, int] | None:
    """One exchange of a type: checks limit and cost, sends the 0x0405 messages; returns (reward id, number)."""
    from accounts import MSG_CURRENCY_CHANGE
    gid, acc = int(client.gid), client.account
    d = state(gid)
    n = d["counts"].get(str(kind), 0) + 1
    most = limit(kind)
    p = price(kind, n)
    if p is None or (most and n > most):
        return None
    cc, cn, rc, rn = p
    if cn and acc.get_currency(cc) < cn:
        return None
    d["counts"][str(kind)] = n
    pvp.save()
    first = [(2, 2, wire.build([(1, 0, kind), (2, 0, n)]))]
    if cn:
        first.insert(0, (1, 2, acc.set_currency(cc, acc.get_currency(cc) - cn)))
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build(first))
    if rc == 105:  # stamina may go over the regeneration cap
        acc.set_currency(rc, acc.get_currency(rc) + rn)
        entry = wire.build([(1, 0, rc), (2, 0, acc.get_currency(rc))])
    else:
        entry = acc.set_currency(rc, acc.get_currency(rc) + rn)
    acc.save()
    await client.send_packet(MSG_CURRENCY_CHANGE, wire.build([(1, 2, entry)]))
    return rc, rn


async def handle_exchange(client, pb: bytes):
    P = _P()
    req = P.CurrencyExchangeC2S.FromString(pb)
    times = max(1, req.num)
    done = 0
    for _ in range(times):
        if await exchange_once(client, req.exchange_type) is None:
            break
        done += 1
    err = 0 if done == times else P.FAILED
    await client.send_packet(MSG_EXCHANGE, P.CurrencyExchangeS2C(err=err, exchange_type=req.exchange_type).SerializeToString())


async def assistant(client, count: int) -> list[tuple[int, int]]:
    """Assistant function 5: exchange stamina until today's count reaches the function's arg (wcy2711: arg 2,
    two free exchanges, unit 5 = (105, 60) twice)."""
    d = state(int(client.gid))
    shown = []
    while d["counts"].get("2", 0) < count:
        got = await exchange_once(client, 2)
        if got is None:
            break
        shown.append(got)
    return shown


HANDLERS = {MSG_EXCHANGE: handle_exchange}
