"""Discord TLDR bot for Railway."""

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

import discord
from discord import app_commands

from summarizer import summarize_conversation


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("bot")


# ------------------------------------------------------------------
# Discord configuration
# ------------------------------------------------------------------

def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()

    intents.message_content = env_flag(
        "MESSAGE_CONTENT_INTENT"
    )

    intents.members = env_flag(
        "MEMBERS_INTENT"
    )

    intents.presences = env_flag(
        "PRESENCES_INTENT"
    )

    return intents


# ------------------------------------------------------------------
# Railway healthcheck
# ------------------------------------------------------------------

def start_health_server(
    get_status,
    host=None,
    port=None,
):
    class HealthHandler(BaseHTTPRequestHandler):

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path not in {
                "/health",
                "/healthz",
                "/ready",
                "/live",
            }:
                self.send_response(404)
                self.end_headers()
                return

            status = get_status()

            if path == "/live":
                ok = status["live"]
            else:
                ok = status["ready"]

            payload = dict(status)
            payload["ok"] = ok

            if ok:
                payload["status"] = "ok"
            else:
                payload["status"] = "unavailable"

            body = json.dumps(
                payload
            ).encode("utf-8")

            if ok:
                self.send_response(200)
            else:
                self.send_response(503)

            self.send_header(
                "Content-Type",
                "application/json",
            )

            self.send_header(
                "Cache-Control",
                "no-store",
            )

            self.send_header(
                "Content-Length",
                str(len(body)),
            )

            self.end_headers()
            self.wfile.write(body)

        def log_message(
            self,
            format,
            *args,
        ):
            return

    if host is None:
        host = os.getenv(
            "HOST",
            "0.0.0.0",
        )

    if port is None:
        port = int(
            os.getenv(
                "PORT",
                "8080",
            )
        )

    server = ThreadingHTTPServer(
        (host, port),
        HealthHandler,
    )

    thread = Thread(
        target=server.serve_forever,
        daemon=True,
        name="healthcheck",
    )

    thread.start()

    log.info(
        "healthcheck listening on port %s",
        server.server_port,
    )

    return server, thread


def stop_health_server(
    server,
    thread,
):
    server.shutdown()
    server.server_close()
    thread.join()


# ------------------------------------------------------------------
# TLDR configuration
# ------------------------------------------------------------------

TIMEFRAMES = {
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}


TIMEFRAME_CHOICES = [
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


# ------------------------------------------------------------------
# Discord bot
# ------------------------------------------------------------------

class Bot(discord.Client):

    def __init__(self):
        super().__init__(
            intents=build_intents()
        )

        self.commands_synced = False
        self.gateway_connected = False
        self.stopping = False

        self.tree = app_commands.CommandTree(
            self
        )

        # ----------------------------------------------------------
        # /ping
        # ----------------------------------------------------------

        @self.tree.command(
            name="ping",
            description="Check that the bot is alive",
        )
        async def ping(
            interaction: discord.Interaction,
        ):
            latency = self.latency

            if math.isfinite(latency):
                latency_ms = str(
                    round(latency * 1000)
                )
            else:
                latency_ms = "unknown"

            await interaction.response.send_message(
                f"Pong! `{latency_ms}ms`",
                ephemeral=True,
            )

        # ----------------------------------------------------------
        # /info
        # ----------------------------------------------------------

        @self.tree.command(
            name="info",
            description="Show bot and library versions",
        )
        async def info(
            interaction: discord.Interaction,
        ):
            await interaction.response.send_message(
                (
                    f"discord.py "
                    f"`{discord.__version__}` "
                    f"· `{self.user}`"
                ),
                ephemeral=True,
            )

        # ----------------------------------------------------------
        # /tldr
        # ----------------------------------------------------------

        @self.tree.command(
            name="tldr",
            description=(
                "Summarize recent messages "
                "in this channel"
            ),
        )
        @app_commands.describe(
            timeframe=(
                "How far back should I read?"
            )
        )
        @app_commands.choices(
            timeframe=TIMEFRAME_CHOICES
        )
        async def tldr(
            interaction: discord.Interaction,
            timeframe: app_commands.Choice[str],
        ):

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            channel = interaction.channel

            if (
                channel is None
                or not hasattr(
                    channel,
                    "history",
                )
            ):
                await interaction.followup.send(
                    (
                        "Η εντολή μπορεί να "
                        "χρησιμοποιηθεί μόνο μέσα "
                        "σε Discord channel."
                    ),
                    ephemeral=True,
                )
                return

            delta = TIMEFRAMES.get(
                timeframe.value
            )

            if delta is None:
                await interaction.followup.send(
                    "Μη έγκυρο timeframe.",
                    ephemeral=True,
                )
                return

            after = (
                discord.utils.utcnow()
                - delta
            )

            messages = []
            participants = set()

            try:

                async for message in channel.history(
                    limit=None,
                    after=after,
                    oldest_first=True,
                ):

                    if message.author.bot:
                        continue

                    content = (
                        message.content.strip()
                    )

                    if not content:
                        continue

                    timestamp = (
                        message.created_at.strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    )

                    author = (
                        message.author.display_name
                    )

                    line = (
                        f"[{timestamp}] "
                        f"{author}: "
                        f"{content}"
                    )

                    messages.append(
                        line
                    )

                    participants.add(
                        message.author.id
                    )

            except discord.Forbidden:

                await interaction.followup.send(
                    (
                        "Δεν έχω permission να "
                        "διαβάσω το history αυτού "
                        "του channel."
                    ),
                    ephemeral=True,
                )

                return

            except discord.HTTPException:

                log.exception(
                    "Failed to retrieve "
                    "Discord message history"
                )

                await interaction.followup.send(
                    (
                        "Παρουσιάστηκε πρόβλημα "
                        "κατά την ανάγνωση "
                        "των μηνυμάτων."
                    ),
                    ephemeral=True,
                )

                return

            if not messages:

                await interaction.followup.send(
                    (
                        f"TL;DR - "
                        f"{timeframe.name}\n\n"
                        "Δεν βρέθηκαν text messages "
                        "σε αυτό το χρονικό διάστημα."
                    ),
                    ephemeral=True,
                )

                return

            log.info(
                (
                    "TLDR request "
                    "guild=%s "
                    "channel=%s "
                    "timeframe=%s "
                    "messages=%s "
                    "participants=%s"
                ),
                interaction.guild_id,
                interaction.channel_id,
                timeframe.value,
                len(messages),
                len(participants),
            )

            conversation = "\n".join(
                messages
            )

            # ------------------------------------------------------
            # Groq summarization
            # ------------------------------------------------------

            try:

                summary = (
                    await summarize_conversation(
                        conversation
                    )
                )

            except Exception:

                log.exception(
                    "AI summarization failed"
                )

                await interaction.followup.send(
                    (
                        "Δεν μπόρεσα να δημιουργήσω "
                        "το TL;DR αυτή τη στιγμή. "
                        "Δοκίμασε ξανά σε λίγο."
                    ),
                    ephemeral=True,
                )

                return

            # ------------------------------------------------------
            # Final response
            # ------------------------------------------------------

            response = (
                f"TL;DR - "
                f"{timeframe.name}\n\n"
                f"{summary}\n\n"
                "--------------------\n"
                f"{len(messages)} messages · "
                f"{len(participants)} participants"
            )

            if len(response) > 1950:

                response = (
                    response[:1850].rstrip()
                    + "\n\n"
                    + (
                        "Το TL;DR περικόπηκε "
                        "λόγω του ορίου μηνύματος "
                        "του Discord."
                    )
                )

            await interaction.followup.send(
                response,
                ephemeral=True,
            )

        # ----------------------------------------------------------
        # Command errors
        # ----------------------------------------------------------

        @self.tree.error
        async def command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ):

            log.error(
                "Slash command failed: %r",
                error,
            )

            text = (
                "Παρουσιάστηκε σφάλμα "
                "κατά την εκτέλεση "
                "της εντολής."
            )

            try:

                if interaction.response.is_done():

                    await interaction.followup.send(
                        text,
                        ephemeral=True,
                    )

                else:

                    await interaction.response.send_message(
                        text,
                        ephemeral=True,
                    )

            except discord.HTTPException:

                log.error(
                    "Could not send "
                    "command error response"
                )

    # --------------------------------------------------------------
    # Health status
    # --------------------------------------------------------------

    def health_status(self):

        connected = (
            self.gateway_connected
            and self.is_ready()
            and not self.is_closed()
        )

        if self.stopping:
            discord_status = "stopping"

        elif connected:
            discord_status = "connected"

        else:
            discord_status = "disconnected"

        return {
            "ready": (
                connected
                and self.commands_synced
                and not self.stopping
            ),
            "live": (
                not self.stopping
            ),
            "discord": discord_status,
            "commandsSynced": (
                self.commands_synced
            ),
        }

    # --------------------------------------------------------------
    # Slash command synchronization
    # --------------------------------------------------------------

    async def setup_hook(self):

        self.commands_synced = False

        guild_id = os.getenv(
            "DISCORD_GUILD_ID",
            "",
        ).strip()

        if guild_id:

            if (
                not guild_id.isdecimal()
                or not (
                    0
                    < int(guild_id)
                    < 2**64
                )
            ):

                raise ValueError(
                    (
                        "DISCORD_GUILD_ID "
                        "must be a positive "
                        "Discord snowflake"
                    )
                )

            guild = discord.Object(
                id=int(guild_id)
            )

            self.tree.copy_global_to(
                guild=guild
            )

            synced = (
                await self.tree.sync(
                    guild=guild
                )
            )

            log.info(
                (
                    "synced %s slash "
                    "command(s) to guild %s"
                ),
                len(synced),
                guild_id,
            )

        else:

            synced = (
                await self.tree.sync()
            )

            log.info(
                (
                    "synced %s global "
                    "slash command(s)"
                ),
                len(synced),
            )

        self.commands_synced = True

    # --------------------------------------------------------------
    # Discord status
    # --------------------------------------------------------------

    async def on_ready(self):

        self.gateway_connected = True

        log.info(
            "logged in as %s (%s)",
            self.user,
            getattr(
                self.user,
                "id",
                "?",
            ),
        )

    async def on_disconnect(self):

        self.gateway_connected = False

    async def on_resumed(self):

        self.gateway_connected = True


# ------------------------------------------------------------------
# Runtime
# ------------------------------------------------------------------

async def run_discord(
    token,
    startup_timeout=25.0,
    shutdown_timeout=5.0,
):

    if not token.strip():

        raise ValueError(
            "DISCORD_TOKEN is required"
        )

    client = Bot()

    server = None
    thread = None
    tasks = []

    try:

        await client.__aenter__()

        server, thread = (
            start_health_server(
                client.health_status
            )
        )

        worker = asyncio.create_task(
            client.start(token)
        )

        ready = asyncio.create_task(
            client.wait_until_ready()
        )

        tasks.extend(
            [worker, ready]
        )

        done, _ = await asyncio.wait(
            tasks,
            timeout=startup_timeout,
            return_when=(
                asyncio.FIRST_COMPLETED
            ),
        )

        if worker in done:

            await worker

            raise RuntimeError(
                (
                    "Discord gateway stopped "
                    "before becoming ready"
                )
            )

        if ready not in done:

            raise TimeoutError(
                (
                    "Discord startup timed out "
                    "before gateway readiness"
                )
            )

        await ready

        if not client.commands_synced:

            raise RuntimeError(
                (
                    "Discord commands "
                    "were not synchronized"
                )
            )

        await worker

        raise RuntimeError(
            (
                "Discord gateway "
                "stopped unexpectedly"
            )
        )

    finally:

        client.stopping = True

        for task in tasks:
            task.cancel()

        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        try:

            await asyncio.wait_for(
                client.close(),
                timeout=shutdown_timeout,
            )

        except asyncio.TimeoutError:

            log.warning(
                "Discord client close timed out"
            )

        if (
            server is not None
            and thread is not None
        ):

            await asyncio.to_thread(
                stop_health_server,
                server,
                thread,
            )


async def serve(token):

    loop = asyncio.get_running_loop()

    task = asyncio.current_task()

    if task is None:

        raise RuntimeError(
            (
                "Could not get current "
                "asyncio task"
            )
        )

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
    ):

        loop.add_signal_handler(
            sig,
            task.cancel,
        )

    try:

        await run_discord(
            token
        )

    finally:

        for sig in (
            signal.SIGINT,
            signal.SIGTERM,
        ):

            loop.remove_signal_handler(
                sig
            )


def main():

    token = os.getenv(
        "DISCORD_TOKEN",
        "",
    ).strip()

    if not token:

        log.error(
            "DISCORD_TOKEN is required"
        )

        return 1

    try:

        asyncio.run(
            serve(token)
        )

    except (
        KeyboardInterrupt,
        asyncio.CancelledError,
    ):

        return 0

    except discord.LoginFailure:

        log.error(
            (
                "DISCORD_TOKEN was "
                "rejected by Discord"
            )
        )

    except discord.PrivilegedIntentsRequired:

        log.error(
            (
                "A privileged intent "
                "is enabled but not enabled "
                "in Discord Developer Portal"
            )
        )

    except Exception:

        log.exception(
            "Discord startup/runtime failed"
        )

    else:

        return 0

    return 1


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
