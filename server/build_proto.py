"""
Build Python protobuf classes from the reconstructed decoded/proto/haikyuu.proto.

The reconstructed proto has wrong field numbers in places and a few conflicts
protoc rejects. This writes a cleaned copy to server/proto_gen/ and compiles it:
- field numbers are replaced by the real ones read from hotfix/Config.dll
  (decoded/proto_tags.json, made by tools_proto_tags.py)
- duplicate enum value names (proto enum values share one namespace) are prefixed
  with their enum name, and every enum allows aliases; wire values are unchanged
- duplicate field numbers inside a message: the later field moves to a free high
  number, so its data is kept as an unknown field and survives re-encoding
"""
import json
import re
import sys
from pathlib import Path

from grpc_tools import protoc

BASE = Path(__file__).parent
SRC = BASE.parent / "decoded" / "proto" / "haikyuu.proto"
OUT = BASE / "proto_gen"

TAGS_FILE = BASE.parent / "decoded" / "proto_tags.json"  # from tools_proto_tags.py


def norm(name: str) -> str:
    return name.replace("_", "").lower()


TAGS = {msg: {norm(k): v for k, v in fields.items()}
        for msg, fields in json.loads(TAGS_FILE.read_text(encoding="utf-8")).items()} if TAGS_FILE.exists() else {}

BLOCK_RE =re.compile(r"^\s*(message|enum|oneof)\s+(\w+)\s*\{")
FIELD_RE = re.compile(r"^(\s*(?:repeated\s+|optional\s+)?[\w.<>, ]+\s+\w+\s*=\s*)(\d+)(.*)$")
ENUM_VAL_RE = re.compile(r"^(\s*)(\w+)(\s*=\s*-?\d+.*)$")


def clean(lines):
    out = []
    stack = []  # [kind, name, used_numbers, index of the block's opening line in out]
    enum_names = set()
    renamed = moved = fixed = 0
    alias_at = []
    for line in lines:
        m = BLOCK_RE.match(line)
        if m:
            kind, name = m.groups()
            if kind == "oneof":
                parent = next(s for s in reversed(stack) if s[0] == "message")
                stack.append(["oneof", name, parent[2], len(out)])
            else:
                stack.append([kind, name, [] if kind == "enum" else set(), len(out)])
            out.append(line)
            continue
        if line.strip().startswith("}") and stack:
            kind, _, used, opened = stack.pop()
            if kind == "enum" and len(used) != len(set(used)):
                alias_at.append(opened)
            out.append(line)
            continue
        if stack and stack[-1][0] == "enum":
            m = ENUM_VAL_RE.match(line)
            if m and m.group(2) not in ("option", "reserved"):
                ind, val, rest = m.groups()
                if val in enum_names:
                    val = f"{stack[-1][1]}_{val}"
                    renamed += 1
                enum_names.add(val)
                stack[-1][2].append(int(re.match(r"\s*=\s*(-?\d+)", rest).group(1)))
                line = f"{ind}{val}{rest}"
        elif stack and stack[-1][0] in ("message", "oneof") and not line.strip().startswith(("//", "option", "reserved")):
            m = FIELD_RE.match(line)
            if m:
                head, num, rest = m.group(1), int(m.group(2)), m.group(3)
                msg = next(s[1] for s in reversed(stack) if s[0] == "message")
                fname = re.search(r"(\w+)\s*=\s*$", head).group(1)
                real = TAGS.get(msg, {}).get(norm(fname))
                if real and real != num:
                    num = real
                    fixed += 1
                    rest = re.sub(r"\s*// field number unconfirmed", "", rest)
                    line = f"{head}{num}{rest}"
                elif real:
                    rest = re.sub(r"\s*// field number unconfirmed", "", rest)
                    line = f"{head}{num}{rest}"
                used = stack[-1][2]
                if num in used:
                    num = 10000 + len(used)
                    while num in used:
                        num += 1
                    moved += 1
                    line = f"{head}{num}{rest}"
                used.add(num)
        out.append(line)
    for i in sorted(alias_at, reverse=True):
        out.insert(i + 1, "  option allow_alias = true;")
    return out, renamed, moved, fixed


def main():
    OUT.mkdir(exist_ok=True)
    lines, renamed, moved, fixed = clean(SRC.read_text(encoding="utf-8").splitlines())
    (OUT / "haikyuu.proto").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"field numbers corrected from Config.dll: {fixed}, enum values renamed: {renamed}, fields moved: {moved}")
    rc = protoc.main(["protoc", f"-I{OUT}", f"--python_out={OUT}", str(OUT / "haikyuu.proto")])
    if rc != 0:
        sys.exit(rc)
    print(f"Compiled {OUT / 'haikyuu_pb2.py'}")


if __name__ == "__main__":
    main()
