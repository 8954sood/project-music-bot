from __future__ import annotations

from typing import Dict

import discord

from core.audio.backend import AudioBackend, OnTrackEnd
from core.model.music_application import MusicApplication

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class FFmpegBackend(AudioBackend):
    def __init__(self) -> None:
        self._players: Dict[int, discord.VoiceClient] = {}

    async def connect(self, bot: discord.Client) -> None:
        return None

    async def ensure_player(self, guild_id: int, voice_channel: discord.VoiceChannel) -> None:
        player = self._players.get(guild_id)
        if player is not None and player.is_connected():
            if player.channel and player.channel.id == voice_channel.id:
                return
            try:
                player.stop()
                await player.disconnect()
            except Exception:
                pass

        player = await voice_channel.connect()
        self._players[guild_id] = player

    async def play(self, guild_id: int, track: MusicApplication, on_end: OnTrackEnd) -> None:
        player = self._players.get(guild_id)
        if player is None or not player.is_connected():
            raise RuntimeError("Voice client is not connected.")

        source = discord.FFmpegPCMAudio(track.youtube_search.audio_source, **FFMPEG_OPTIONS)
        player.play(source, after=on_end)

    async def stop(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        player.stop()

    async def pause(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        player.pause()

    async def resume(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        player.resume()

    async def skip(self, guild_id: int) -> None:
        await self.stop(guild_id)

    async def set_volume(self, guild_id: int, volume: int) -> None:
        player = self._players.get(guild_id)
        if player is None or player.source is None:
            return
        source = player.source
        if hasattr(source, "volume"):
            source.volume = max(min(volume / 100, 2.0), 0.0)

    async def is_playing(self, guild_id: int) -> bool:
        player = self._players.get(guild_id)
        return bool(player and player.is_playing())

    async def disconnect(self, guild_id: int) -> None:
        player = self._players.pop(guild_id, None)
        if player is None:
            return
        try:
            player.stop()
            await player.disconnect()
        except Exception:
            pass
