"""Protocol-only local relay for an external test Agent without MCP attachment.

No navigation, automatic acknowledgement, task creation or action selection lives
here. Each client request forwards exactly one MCP call; image bytes are unchanged.
Run a separate process and socket for each paired Brain. Credentials stay in NERV.
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import time


async def serve(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    params = StdioServerParameters(command=args.python, args=["-m", "nerv.platform.cli", "mcp",
        "--channel", args.channel, "--client", args.client] + (["--brain", args.brain] if args.brain else []), env=dict(os.environ))
    with (output / "bridge.stderr").open("w") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                counter = 0
                lock = asyncio.Lock()

                async def handle(reader, writer):
                    nonlocal counter
                    async with lock:
                        counter += 1
                        event = {"sequence": counter, "started_at": time.time(),
                                 "started_monotonic": time.monotonic()}
                        try:
                            request = json.loads(await reader.readline())
                            event["request"] = request
                            # Record before forwarding, including rejected/retried
                            # calls and requests interrupted before any response.
                            with (output / "requests.jsonl").open("a") as journal:
                                journal.write(json.dumps(event, ensure_ascii=False) + "\n")
                                journal.flush()
                                os.fsync(journal.fileno())
                            if request["operation"] == "list_tools":
                                result = (await session.list_tools()).model_dump(mode="json")
                            else:
                                response = await session.call_tool(request["operation"], request.get("arguments", {}))
                                result = response.model_dump(mode="json")
                                for index, block in enumerate(result.get("content", [])):
                                    if block.get("type") == "image":
                                        image = output / f"{counter:04d}-{index}.png"
                                        image.write_bytes(base64.b64decode(block.pop("data")))
                                        block["path"] = str(image)
                            event["result"] = result
                        except Exception as error:
                            event["error"] = f"{type(error).__name__}: {error}"
                        event["completed_at"] = time.time()
                        event["completed_monotonic"] = time.monotonic()
                        with (output / "protocol.jsonl").open("a") as journal:
                            journal.write(json.dumps(event, ensure_ascii=False) + "\n")
                        writer.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                        await writer.drain()
                        writer.close()
                        await writer.wait_closed()

                server = await asyncio.start_unix_server(handle, path=args.socket)
                os.chmod(args.socket, 0o600)
                print(json.dumps({"ready": True, "socket": args.socket}), flush=True)
                async with server:
                    await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    host = modes.add_parser("serve")
    for key in ("socket", "python", "client", "output"):
        host.add_argument("--" + key, required=True)
    host.add_argument("--brain")
    host.add_argument("--channel", default="stable", choices=["stable", "preview"])
    client = modes.add_parser("call")
    client.add_argument("--socket", required=True)
    client.add_argument("operation")
    client.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    if args.mode == "serve":
        asyncio.run(serve(args))
    else:
        request = {"operation": args.operation, "arguments": json.loads(args.arguments)}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(120)
            connection.connect(args.socket)
            connection.sendall((json.dumps(request) + "\n").encode())
            print(connection.makefile().readline(), end="")


if __name__ == "__main__":
    main()
