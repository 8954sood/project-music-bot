from dataclasses import dataclass
from typing import List

from .youtube_search import YoutubeSearch


@dataclass
class YoutubePlaylist:
    title: str
    song_cnt: int
    songs: List[YoutubeSearch]