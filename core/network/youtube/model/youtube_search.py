from dataclasses import dataclass

@dataclass
class YoutubeSearch:
    audio_source: str #url
    title: str # fulltitle
    thumbnail_url: str #thumbnail
    duration: int
    duration_string: str # duration_string
    video_id: str # display_id
    video_url: str
    channel_id: str # @viberefuel
    channel_url: str # uploader_url
    channel_name: str # uploader