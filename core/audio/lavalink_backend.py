from __future__ import annotations

import asyncio
from typing import Dict

import discord

from core.audio.backend import AudioBackend, OnTrackEnd
from core.model.music_application import MusicApplication


class LavalinkBackend(AudioBackend):
    def __init__(self, *, host: str, port: int, password: str, identifier: str) -> None:
        try:
            import pomice
        except ImportError as exc:  # pragma: no cover - requires optional dependency
            raise RuntimeError("Pomice is required for the Lavalink backend.") from exc

        self._pomice = pomice
        self._node = None
        self._players: Dict[int, pomice.Player] = {}
        self._monitor_tasks: Dict[int, asyncio.Task] = {}
        self._host = host
        self._port = port
        self._password = password
        self._identifier = identifier

    async def connect(self, bot: discord.Client) -> None:
        self._node = await self._pomice.NodePool.create_node(
            bot=bot,
            host=self._host,
            port=self._port,
            identifier=self._identifier,
            password=self._password,
        )

    async def ensure_player(self, guild_id: int, voice_channel: discord.VoiceChannel) -> None:
        player = self._players.get(guild_id)
        if player is not None and (player.is_connected() if callable(player.is_connected) else player.is_connected):
            if player.guild and player.guild.voice_client and player.guild.voice_client.channel:
                if player.guild.voice_client.channel.id == voice_channel.id:
                    return
            await player.move_to(voice_channel)
            return

        player = await voice_channel.connect(cls=self._pomice.Player)
        self._players[guild_id] = player

    async def play(self, guild_id: int, track: MusicApplication, on_end: OnTrackEnd) -> None:
        player = self._players.get(guild_id)
        if player is None or not (player.is_connected() if callable(player.is_connected) else player.is_connected):
            raise RuntimeError("Lavalink player is not connected.")

        if hasattr(player, "get_tracks"):
            results = await player.get_tracks(query=track.youtube_search.video_url)
        else:
            if self._node is None:
                raise RuntimeError("Lavalink node is not ready.")
            results = await self._node.get_tracks(query=track.youtube_search.video_url)
        if results is None:
            raise RuntimeError("No Lavalink tracks found.")

        if isinstance(results, list):
            tracks = results
        elif hasattr(results, "tracks"):
            tracks = list(results.tracks)
        else:
            tracks = []

        if not tracks:
            raise RuntimeError("No Lavalink tracks found.")

        await player.play(track=tracks[0])
        self._start_monitor(guild_id, player, on_end)

    def _start_monitor(self, guild_id: int, player, on_end: OnTrackEnd) -> None:
        task = self._monitor_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

        async def _monitor() -> None:
            while True:
                await asyncio.sleep(1)
                if getattr(player, "is_dead", False):
                    break
                is_playing = player.is_playing() if callable(player.is_playing) else player.is_playing
                is_paused = player.is_paused() if callable(player.is_paused) else player.is_paused
                if not is_playing and not is_paused and player.current is None:
                    break
            on_end(None)

        self._monitor_tasks[guild_id] = asyncio.create_task(_monitor())

    async def stop(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.stop()

    async def pause(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.set_pause(True)

    async def resume(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.set_pause(False)

    async def skip(self, guild_id: int) -> None:
        await self.stop(guild_id)

    async def set_volume(self, guild_id: int, volume: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.set_volume(volume)

    async def is_playing(self, guild_id: int) -> bool:
        player = self._players.get(guild_id)
        if player is None:
            return False
        return bool(player.is_playing() if callable(player.is_playing) else player.is_playing)

    async def disconnect(self, guild_id: int) -> None:
        task = self._monitor_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

        player = self._players.pop(guild_id, None)
        if player is None:
            return
        await player.destroy()
