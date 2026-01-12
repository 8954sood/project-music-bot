import asyncio
from typing import Optional, Union, List
import yt_dlp

from core import IS_DEBUG
from core.network import YoutubePlaylist
from core.network.youtube import YoutubeSearch
from core.network.youtube.mapper.youtube_search_mapper import dict_to_youtube_search
from core.network.youtube.internal.youtube_utile import is_youtube_url, is_playlist_url, get_song_url

class YoutubeService:

    YDL_OPTIONS = {
        'quiet': not IS_DEBUG,
        "format": "bestaudio[protocol=https]/bestaudio[protocol!=m3u8_native]/bestaudio/best",#"bestaudio/best",
        #"simulate": True,
        "skip_download": True,
        #"postprocessors": [{'key': 'FFmpegExtractAudio','preferredcodec': "mp3",'preferredquality': '192'}],
        'cookiefile': './cookies.txt'
    }

    @staticmethod
    async def search(query: str) -> Union[Optional[YoutubeSearch], Optional[YoutubePlaylist]]:
        if not is_youtube_url(query):
            return await YoutubeService.title_search(query)
        if is_playlist_url(query):
            return await YoutubeService.playlist_search(query)
        return await YoutubeService.url_search(get_song_url(query))


    @staticmethod
    async def url_search(url: str) -> Optional[YoutubeSearch]:
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(YoutubeService.YDL_OPTIONS) as ytdl:
                ytdl.cookiejar.load('./cookies.txt', ignore_discard=True, ignore_expires=True)
                data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(url, download=False)
                )
                return dict_to_youtube_search(data)
        except (yt_dlp.utils.ExtractorError, yt_dlp.utils.DownloadError):
            return None


    @staticmethod
    async def title_search(title: str) -> Optional[YoutubeSearch]:
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(YoutubeService.YDL_OPTIONS) as ytdl:
                ytdl.cookiejar.load('./cookies.txt', ignore_discard=True, ignore_expires=True)
                data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(f"ytsearch:{title}", download=False)
                )
                return dict_to_youtube_search(data['entries'][0])
        except (yt_dlp.utils.ExtractorError, yt_dlp.utils.DownloadError):
            return None

    @staticmethod
    async def playlist_search(playlist_url: str) -> Optional[YoutubePlaylist]:
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(YoutubeService.YDL_OPTIONS) as ytdl:
                ytdl.cookiejar.load('./cookies.txt', ignore_discard=True, ignore_expires=True)
                data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(playlist_url, download=False)
                )
                # 곡 리스트 추출
                if 'entries' in data:
                    songs = data['entries']
                    mapping_songs = []
                    # 각 곡 정보 출력
                    for i in songs:
                        song = dict_to_youtube_search(i)

                        if song is not None:
                            mapping_songs.append(song)

                    if len(mapping_songs) == 0:
                        return None

                    return YoutubePlaylist(
                        title=data.get('title', '알 수 없음'),
                        song_cnt=len(mapping_songs),
                        songs=mapping_songs
                    )
                return None
        except (yt_dlp.utils.ExtractorError, yt_dlp.utils.DownloadError):
            return None


if __name__ == "__main__":
    async def main():
        urls = [
            "https://www.youtube.com/playlist?list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6",
            "https://www.youtube.com/playlist?list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6",
            "https://youtube.com/playlist?list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6&si=RAUs33iGEwQhKhmj",
            "https://www.youtube.com/watch?v=htALtC8nZiQ",
            "https://www.youtube.com/watch?v=mBXBOLG06Wc&list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6&index=1",
            "https://youtu.be/mBXBOLG06Wc?si=rOtXyr4ST31MJvzK",
            "https://www.youtube.com/live/4Df04ViiX5U"
        ]
        for i in urls:
            print(await YoutubeService.search(i))
    asyncio.run(main())