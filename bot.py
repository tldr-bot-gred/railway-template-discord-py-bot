"""Discord TLDR bot with Railway readiness and liveness endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable

import discord
from discord import app_commands

from summarizer import summarize_conversation


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()

    intents.message_content = env_flag("MESSAGE_CONTENT_INTENT")
    intents.members = env_flag("MEMBERS_INTENT")
    intents.presences = env_flag("PRESENCES_INTENT")

    return intents


def start_health_server(
    get_status: Callable[[], dict],
    *,
    host: str | None = None,
    port: int | None = None,
):

    class HealthHandler(BaseHTTPRequestHandler):

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]

            if path not in {"/health", "/healthz", "/ready", "/live"}:
                self.send_response(404)
                self.end_headers()
                return

            status = get_status()
            ok = status["live"] if path == "/live" else status["ready"]

            body = json.dumps(
                {
                    **status,
                    "ok": ok,
                    "status": "ok" if ok else "unavailable",
                }
            ).encode()

            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    host = host if host is not None else os.getenv("HOST", "0.0.0.0")
    port = port if port is not None else int(os.getenv("PORT", "8080"))

    server = ThreadingHTTPServer((host, port), HealthHandler)

   thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
log.info("healthcheck listening on port %s", server.server_port)
``
