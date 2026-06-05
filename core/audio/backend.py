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

    @abstractmethod
    async def close(self) -> None:
        """백엔드가 등록한 백그라운드 작업 정리(cog 언로드/리로드 시 호출).

        정리할 게 없는 백엔드는 빈 구현(pass)을 둔다.
        """
        pass
