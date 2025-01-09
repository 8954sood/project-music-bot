from typing import List, Optional

from discord import VoiceClient
from typing_extensions import TypedDict

from core.model.music_application import MusicApplication


class MusicType(TypedDict):
    guild_id: int
    voice_channel_id: int
    vc: VoiceClient
    volume: float
    now_playing: Optional[MusicApplication]
    queue: List[MusicApplication]
    loop: bool
    is_playing: bool