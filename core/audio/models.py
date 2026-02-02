from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from core.model.music_application import MusicApplication


class RepeatMode(Enum):
    NONE = 0
    ONE = 1
    ALL = 2


@dataclass
class AudioState:
    guild_id: int
    voice_channel_id: int
    now_playing: Optional[MusicApplication]
    queue: List[MusicApplication]
    volume: float
    loop: bool
    is_paused: bool


@dataclass
class AudioStatus:
    now_playing: Optional[MusicApplication]
    queue: List[MusicApplication]
    loop: bool
    is_paused: bool
