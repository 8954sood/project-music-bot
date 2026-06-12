from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from core.network import YoutubeSearch

if TYPE_CHECKING:
    from core.audio.startup_timer import PlayStartupTimer


@dataclass
class MusicApplication:
    youtube_search: YoutubeSearch
    user_name: str
    user_icon: str
    user_id: int
    lavalink_track: Optional[object] = None
    lavalink_node_identifier: Optional[str] = None
    startup_timer: Optional[PlayStartupTimer] = None
