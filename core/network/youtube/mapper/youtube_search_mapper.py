from typing import Optional
import traceback
from core.network.youtube import YoutubeSearch

def dict_to_youtube_search(target: dict) -> Optional[YoutubeSearch]:
    try:
        return YoutubeSearch(
            title=target["title"],
            audio_source=target["url"],
            thumbnail_url=target["thumbnail"],
            duration=target["duration"],
            duration_string=target["duration_string"],
            video_id=target["display_id"],
            video_url=target["webpage_url"],
            channel_id=target["uploader_id"],
            channel_name=target["uploader"],
            channel_url=target["uploader_url"],
        )
    except:
        traceback.print_exc()
        return None