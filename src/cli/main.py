"""Orca CLI：常驻运行与本机控制接口分离。"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal

from control.application import ControlApplication
from control.config import load_config


async def _request(socket_path: str, payload: dict) -> None:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        response = json.loads(await reader.readline())
        print(json.dumps(response, ensure_ascii=False, indent=2))
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "request failed"))
    finally:
        writer.close()
        await writer.wait_closed()


async def _run(config_path: str) -> None:
    config = load_config(config_path)
    app = ControlApplication(config)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await app.start()
    try:
        await stop_event.wait()
    finally:
        await app.close()


def main() -> None:
    """解析参数并执行启动、校验或控制操作。"""
    parser = argparse.ArgumentParser(prog="orca")
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run")
    valid = commands.add_parser("validate-config")
    status = commands.add_parser("status")
    start = commands.add_parser("start")
    start.add_argument("task")
    stop = commands.add_parser("stop")
    stop.add_argument("task", nargs="?")
    stop.add_argument("--all", action="store_true")
    for command in (run, valid, status, start, stop):
        command.add_argument("--config", default="configs/template")
    args = parser.parse_args()
    if args.action == "validate-config":
        config = load_config(args.config)
        print(f"valid: site={config.site.site_id}; tasks={len(config.tasks)}")
    elif args.action == "run":
        asyncio.run(_run(args.config))
    else:
        config = load_config(args.config)
        payload = {"action": args.action}
        if hasattr(args, "task") and args.task:
            payload["task"] = args.task
        if args.action == "stop":
            if not args.all and not args.task:
                parser.error("stop requires a task or --all")
            payload["all"] = args.all
        asyncio.run(_request(config.runtime.socket_path, payload))


if __name__ == "__main__":
    main()
