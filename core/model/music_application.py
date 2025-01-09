from dataclasses import dataclass

from core.network import YoutubeSearch


@dataclass
class MusicApplication:
    youtube_search: YoutubeSearch
    user_name: str
    user_icon: str
    user_id: int