"""
Tutorial (beginner guide) for new players.

The guide is run by the client: guidegroupconfig.json says when a group starts (a window opens, the
player's level, a stage not cleared yet ...). The server only keeps which groups are done:
0x1F01 UserGuideInfoS2C{done_group_ids} at login, 0x1F02 UserGuideDoneC2S{group_id} -> S2C{group_id}
when the client finishes one.

New players (starter baseline) used to get the captured account's list (every group done), so the
tutorial never came. Now they start with only SKIPPED done; the captured account keeps its list.
Group 0 is the opening match (trigger "Skill 51109"), a scripted match this server does not
replay; it stays skipped so a new player is never stuck in it.
"""
import accounts
import matches
import pvp

P = matches.P
MSG_INFO, MSG_DONE = 0x1F01, 0x1F02
SKIPPED = [0]


def _store() -> dict:
    return pvp.state().setdefault("guide", {})


def done(gid: int, acc) -> list[int]:
    if acc.b is accounts.CAPTURED:
        return list(P.UserGuideInfoS2C.FromString(acc.b.pb("0x1F01")).done_group_ids)
    return sorted(set(SKIPPED) | set(_store().get(str(gid), [])))


def info(gid: int, acc) -> bytes:
    return P.UserGuideInfoS2C(done_group_ids=done(gid, acc)).SerializeToString()


async def handle_done(client, pb: bytes):
    group = P.UserGuideDoneC2S.FromString(pb).group_id
    mine = _store().setdefault(str(client.gid), [])
    if group not in mine:
        mine.append(group)
        pvp.save()
    await client.send_packet(MSG_DONE, P.UserGuideDoneS2C(group_id=group).SerializeToString())


# Group 3 (guided first recruit, guideconfig 302): the step ends when the player owns card 1140
# (EndTriggers PlayerExist 1140) and the client then shows that card as the result
# (OpenGachaResult 1140); groups 4-6 level it up and put it into the line-up. So that single pull
# gives 1140 for sure.
RECRUIT_GROUP, RECRUIT_CARD = 3, 1140


def forced_draw(gid: int, acc, count: int) -> list[int]:
    """Cards the tutorial needs from this pull (put first), [] when none."""
    if acc.b is accounts.CAPTURED or RECRUIT_GROUP in done(gid, acc):
        return []
    if RECRUIT_CARD in acc.owned_conf_ids():
        return []
    return [RECRUIT_CARD]


HANDLERS = {MSG_DONE: handle_done}
