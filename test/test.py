import asyncio

import yt_dlp

YDL_OPTIONS = {
    'quiet': False,
    "format": "bestaudio/best",
    "simulate": True,
    "skip_download": True,
    "postprocessors": [{'key': 'FFmpegExtractAudio','preferredcodec': "mp3",'preferredquality': '192'}],
    'cookiefile': './cookies.txt',
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    },
}

async def main():

    loop = asyncio.get_event_loop()
    try:
        with yt_dlp.YoutubeDL() as ytdl:
            ytdl.cookiejar.load('./cookies.txt', ignore_discard=True, ignore_expires=True)
            for cookie in ytdl.cookiejar:
                print(cookie)
            data = await loop.run_in_executor(
                None,
                lambda: ytdl.extract_info(f"ytsearch:멜론차트", download=False)
            )
            # print(data)
            print(ytdl.cookiejar.filename)
    except (yt_dlp.utils.ExtractorError, yt_dlp.utils.DownloadError):
        return None
    # print(await YoutubeService.search("멜론차트"))

asyncio.run(main())