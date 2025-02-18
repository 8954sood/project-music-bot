import re
from typing import Optional


def is_youtube_url(text: str) -> bool:
    # 유튜브 관련 URL 정규식
    pattern = re.compile(
        r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/', re.IGNORECASE
    )
    return re.match(pattern, text) is not None

def is_playlist_url(url: str) -> bool:
    # YouTube 플레이리스트 URL 정규식
    pattern = re.compile(r'youtube\.com/playlist\?list=[^&]+', re.IGNORECASE)
    return re.search(pattern, url) is not None


def get_song_url(url: str) -> Optional[str]:
    # 유튜브 동영상 URL 패턴: watch?v=, youtu.be/ 및 live/ 지원
    pattern = (
        r"(?:https://www\.youtube\.com/watch\?v=(?P<id>[^&]+)|"  # watch?v=...
        r"https://youtu\.be/(?P<id2>[^&]+)|"  # youtu.be/...
        r"https://www\.youtube\.com/live/(?P<live_id>[^&/]+))"  # live/...
    )

    match = re.search(pattern, url)
    if match:
        # 먼저 watch?v= 또는 youtu.be/ 에서 video id 추출, 없으면 live_id 사용
        video_id = match.group("id") or match.group("id2") or match.group("live_id")
        return f"https://www.youtube.com/watch?v={video_id}"
    return None

if __name__ == '__main__':
    print(is_youtube_url("https://www.youtube.com/playlist?list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6"))
    print(is_playlist_url("https://www.youtube.com/playlist?list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6"))
    print(is_playlist_url("https://youtube.com/playlist?list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6&si=RAUs33iGEwQhKhmj"))
    print(is_playlist_url("https://www.youtube.com/watch?v=htALtC8nZiQ"))
    print(get_song_url("https://www.youtube.com/watch?v=mBXBOLG06Wc&list=PLg3uhUAs7P6o_mdJI3T2XZsGVTmn3g7g6&index=1"))
    print(get_song_url("https://youtu.be/mBXBOLG06Wc?si=rOtXyr4ST31MJvzK"))
    print(get_song_url("https://www.youtube.com/live/4Df04ViiX5U"))