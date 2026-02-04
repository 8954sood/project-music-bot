from __future__ import annotations

import discord

from core.audio.backend import AudioBackend, OnTrackEnd
from core.audio.ffmpeg_backend import FFmpegBackend
from core.model.music_application import MusicApplication


class HybridBackend(AudioBackend):
    def __init__(self, *, host: str, port: int, password: str, identifier: str) -> None:
        try:
            import pomice
        except ImportError as exc:  # pragma: no cover - requires optional dependency
            raise RuntimeError("Pomice is required for the Hybrid backend.") from exc

        self._pomice = pomice
        self._node = None
        self._ffmpeg = FFmpegBackend()
        self._host = host
        self._port = port
        self._password = password
        self._identifier = identifier

    async def connect(self, bot: discord.Client) -> None:
        await self._ffmpeg.connect(bot)
        self._node = await self._pomice.NodePool.create_node(
            bot=bot,
            host=self._host,
            port=self._port,
            identifier=self._identifier,
            password=self._password,
        )

    async def ensure_player(self, guild_id: int, voice_channel: discord.VoiceChannel) -> None:
        await self._ffmpeg.ensure_player(guild_id, voice_channel)

    async def play(self, guild_id: int, track: MusicApplication, on_end: OnTrackEnd) -> None:
        await self._ffmpeg.play(guild_id, track, on_end)

    async def stop(self, guild_id: int) -> None:
        await self._ffmpeg.stop(guild_id)

    async def pause(self, guild_id: int) -> None:
        await self._ffmpeg.pause(guild_id)

    async def resume(self, guild_id: int) -> None:
        await self._ffmpeg.resume(guild_id)

    async def skip(self, guild_id: int) -> None:
        await self._ffmpeg.skip(guild_id)

    async def set_volume(self, guild_id: int, volume: int) -> None:
        await self._ffmpeg.set_volume(guild_id, volume)

    async def is_playing(self, guild_id: int) -> bool:
        return await self._ffmpeg.is_playing(guild_id)

    async def disconnect(self, guild_id: int) -> None:
        await self._ffmpeg.disconnect(guild_id)
