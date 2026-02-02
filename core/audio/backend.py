from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

import discord

from core.model.music_application import MusicApplication


OnTrackEnd = Callable[[Optional[Exception]], None]


class AudioBackend(ABC):
    @abstractmethod
    async def connect(self, bot: discord.Client) -> None:
        pass

    @abstractmethod
    async def ensure_player(self, guild_id: int, voice_channel: discord.VoiceChannel) -> None:
        pass

    @abstractmethod
    async def play(self, guild_id: int, track: MusicApplication, on_end: OnTrackEnd) -> None:
        pass

    @abstractmethod
    async def stop(self, guild_id: int) -> None:
        pass

    @abstractmethod
    async def pause(self, guild_id: int) -> None:
        pass

    @abstractmethod
    async def resume(self, guild_id: int) -> None:
        pass

    @abstractmethod
    async def skip(self, guild_id: int) -> None:
        pass

    @abstractmethod
    async def set_volume(self, guild_id: int, volume: int) -> None:
        pass

    @abstractmethod
    async def is_playing(self, guild_id: int) -> bool:
        pass

    @abstractmethod
    async def disconnect(self, guild_id: int) -> None:
        pass
