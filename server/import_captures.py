"""
Turns the decrypted captures of Haikyu-Fly-High-Analyse (capture/decrypted_*/NNNN_<module>_<cmd>.bin,
one message body per file) into the match_msgs.json format calibrate.py / buffdiff.py read:
decoded/<name>_match_msgs.json = [{dir, offset, msg_id, hex}].

  python import_captures.py "D:\\2nd Brain\\Haikyu-Fly-High-Analyse\\capture"

Only the match messages are kept (0x0302 room, 0x0303 rallies and the replies that carry frames). The
files hold account data of a real match, so they stay out of the repository (decoded/*match_msgs.json
is not tracked).
"""
import json
import sys
from pathlib import Path

import matches

P = matches.P
KEEP = {0x0302, 0x0303, 0x0307, 0x0309, 0x030A, 0x030B}
OUT = Path(__file__).parent.parent / "decoded"


def convert(folder: Path) -> tuple[int, int]:
    msgs, rooms, rallies = [], 0, 0
    for f in sorted(folder.glob("*.bin")):
        parts = f.stem.split("_")
        if len(parts) != 3:
            continue
        msg_id = int(parts[1]) << 8 | int(parts[2])
        if msg_id not in KEEP:
            continue
        raw = f.read_bytes()
        body = raw[1:] if raw[:1] == b"\x00" else raw
        try:  # server messages only: the request of the same id is small and has no frames / room
            if msg_id == 0x0302:
                m = P.PVPCreateS2C.FromString(body)
                if not m.info.home.formation.players:
                    continue
                rooms += 1
            else:
                cls = {0x0303: P.PVPMatchDataS2C, 0x0307: P.PVPHandleServeS2C, 0x0309: P.PVPFormationChangeS2C,
                       0x030A: P.PVPUltimateSkillS2C, 0x030B: P.PVPMoraleSkillS2C}[msg_id]
                m = cls.FromString(body)
                if msg_id != 0x0309 and not len(m.frames):
                    continue
                if msg_id == 0x0303:
                    rallies += 1
        except Exception:
            continue
        msgs.append({"dir": "S->C", "offset": int(parts[0]), "msg_id": f"0x{msg_id:04X}", "hex": raw.hex()})
    if rooms and rallies:
        name = folder.name.replace("decrypted_", "hfa_").replace("dec_", "hfa_")
        (OUT / f"{name}_match_msgs.json").write_text(json.dumps(msgs))
    return rooms, rallies


if __name__ == "__main__":
    src = Path(sys.argv[1])
    for folder in sorted(p for p in src.iterdir() if p.is_dir()):
        rooms, rallies = convert(folder)
        print(f"{folder.name:28s} rooms {rooms:2d} rallies {rallies:3d}")
