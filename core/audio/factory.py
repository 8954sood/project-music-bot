from __future__ import annotations

import asyncio

import discord

from core.audio.ffmpeg_backend import FFmpegBackend
from core.audio.lavalink_backend import LavalinkBackend
from core.audio.service import AudioService
from core.config import AUDIO_BACKEND, LAVALINK_HOST, LAVALINK_IDENTIFIER, LAVALINK_PASSWORD, LAVALINK_PORT


def create_audio_service(bot: discord.Client) -> AudioService:
    if AUDIO_BACKEND == "lavalink":
        backend = LavalinkBackend(
            host=LAVALINK_HOST,
            port=LAVALINK_PORT,
            password=LAVALINK_PASSWORD,
            identifier=LAVALINK_IDENTIFIER,
        )
    else:
        backend = FFmpegBackend()

    service = AudioService(backend, bot.loop)
    asyncio.run_coroutine_threadsafe(service.connect(bot), bot.loop)
    return service
