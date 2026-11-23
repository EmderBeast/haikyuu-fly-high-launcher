"""
TCP relay for playing on a remote private server.

The emulator setup stays as for a local server (iptables DNAT to 127.0.0.1:10104, adb reverse to
the PC); instead of server.py this relay listens on the PC's port 10104 and forwards every
connection unchanged to the remote server.

Usage: python relay.py HOST[:PORT] [--listen PORT]
"""
import asyncio
import sys

PORT = 10104


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


def parse_target(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":") if ":" in target else (target, "", "")
    return host, int(port) if port else PORT


async def serve(host: str, port: int, listen: int = PORT):
    async def on_client(c_reader, c_writer):
        peer = c_writer.get_extra_info("peername")
        try:
            s_reader, s_writer = await asyncio.wait_for(asyncio.open_connection(host, port), 15)
        except (OSError, asyncio.TimeoutError) as e:
            print(f"[relay] {peer}: {host}:{port} nicht erreichbar ({e})", flush=True)
            c_writer.close()
            return
        print(f"[relay] {peer} -> {host}:{port}", flush=True)
        await asyncio.gather(_pipe(c_reader, s_writer), _pipe(s_reader, c_writer))
        print(f"[relay] {peer} getrennt", flush=True)

    server = await asyncio.start_server(on_client, "127.0.0.1", listen)
    print(f"[relay] 127.0.0.1:{listen} -> {host}:{port}", flush=True)
    async with server:
        await server.serve_forever()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    listen = PORT
    if "--listen" in argv:
        i = argv.index("--listen")
        listen = int(argv[i + 1])
        del argv[i:i + 2]
    if not argv:
        sys.exit(__doc__)
    asyncio.run(serve(*parse_target(argv[0]), listen))


if __name__ == "__main__":
    main()
