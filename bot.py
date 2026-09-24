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


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("bot")


# ---------------------------------------------------------------------------
# Discord intents
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Railway healthcheck server
# ---------------------------------------------------------------------------

def start_health_server(
    get_status: Callable[[], dict],
    *,
    host: str | None = None,
    port: int | None = None,
) -> tuple[ThreadingHTTPServer, Thread\]:

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

    thread = Thread(
        target=lambda: server.serve_forever(poll_interval=0.1),
        daemon=True,
        name="healthcheck",
    )

    thread.start()

    log.info("healthcheck listening on port %s", server.server_port)

    return server, thread


def stop_health_server(
    server: ThreadingHTTPServer,
    thread: Thread,
) -> None:

    server.shutdown()
    server.server_close()
    thread.join()


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

class Bot(discord.Client):

    def __init__(self) -> None:
        super().__init__(intents=build_intents())

        self.commands_synced = False
        self.gateway_connected = False
        self.stopping = False

        self.tree = app_commands.CommandTree(self)

        # -------------------------------------------------------------------
        # /ping
        # -------------------------------------------------------------------

        @self.tree.command(
            name="ping",
            description="Check that the bot is alive",
        )
        async def ping(interaction: discord.Interaction) -> None:

            latency = self.latency

            latency_ms = (
                str(round(latency * 1000))
                if math.isfinite(latency)
                else "unknown"
            )

            await interaction.response.send_message(
                f"Pong! `{latency_ms}ms`",
                ephemeral=True,
            )

        # -------------------------------------------------------------------
        # /info
        # -------------------------------------------------------------------

        @self.tree.command(
            name="info",
            description="Show bot and library versions",
        )
        async def info(interaction: discord.Interaction) -> None:

            await interaction.response.send_message(
                f"discord.py `{discord.__version__}` · `{self.user}`",
                ephemeral=True,
            )

        # -------------------------------------------------------------------
        # /tldr
        # -------------------------------------------------------------------

        @self.tree.command(
            name="tldr",
            description="Summarize recent messages in this channel",
        )
        @app_commands.describe(
            timeframe="How far back should I read?"
        )
        @app_commands.choices(
            timeframe=[
                app_commands.Choice(
                    name="Last 30 minutes",
                    value="30m",
                ),
                app_commands.Choice(
                    name="Last 1 hour",
                    value="1h",
                ),
                app_commands.Choice(
                    name="Last 2 hours",
                    value="2h",
                ),
                app_commands.Choice(
                    name="Last 4 hours",
                    value="4h",
                ),
                app_commands.Choice(
                    name="Last 8 hours",
                    value="8h",
                ),
                app_commands.Choice(
                    name="Last 12 hours",
                    value="12h",
                ),
                app_commands.Choice(
                    name="Last 24 hours",
                    value="24h",
                ),
                app_commands.Choice(
                    name="Last 3 days",
                    value="3d",
                ),
                app_commands.Choice(
                    name="Last 7 days",
                    value="7d",
                ),
            ]
        )
        async def tldr(
            interaction: discord.Interaction,
            timeframe: app_commands.Choice[str],
        ) -> None:

            # Immediately acknowledge the request.
            # Everything that follows remains visible only to the requester.
            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            channel = interaction.channel

            if channel is None or not hasattr(channel, "history"):
                await interaction.followup.send(
                    "❌ Η εντολή μπορεί να χρησιμοποιηθεί μόνο μέσα σε Discord channel.",
                    ephemeral=True,
                )
                return

            # Convert the selected timeframe into a timedelta.
            timeframe_map = {
                "30m": timedelta(minutes=30),
                "1h": timedelta(hours=1),
                "2h": timedelta(hours=2),
                "4h": timedelta(hours=4),
                "8h": timedelta(hours=8),
                "12h": timedelta(hours
