import re

# Spotify 링크 판별. open.spotify.com/(intl-xx/)?(track|album|playlist|artist)/<id> 및 spotify: URI.
#   - 공유 링크의 ?si=... 쿼리스트링, 끝 슬래시 허용.
#   - intl-ko 같은 로케일 prefix 허용.
_SPOTIFY_URL = re.compile(
    r"^(?:https?://)?open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?"
    r"(?P<kind>track|album|playlist|artist)/[a-zA-Z0-9]+",
    re.IGNORECASE,
)
_SPOTIFY_URI = re.compile(
    r"^spotify:(?P<kind>track|album|playlist|artist):[a-zA-Z0-9]+",
    re.IGNORECASE,
)

# 다중 트랙(전체 큐잉 대상) 종류. 단일 트랙(track)은 제외.
_SPOTIFY_COLLECTION_KINDS = {"album", "playlist", "artist"}


def _spotify_kind(text: str):
    if not text:
        return None
    text = text.strip()
    match = _SPOTIFY_URL.match(text) or _SPOTIFY_URI.match(text)
    return match.group("kind").lower() if match else None


def is_spotify_url(text: str) -> bool:
    """Spotify 트랙/앨범/플레이리스트/아티스트 링크(또는 spotify: URI)인지."""
    return _spotify_kind(text) is not None


def is_spotify_collection_url(text: str) -> bool:
    """Spotify 앨범/플레이리스트/아티스트(= 다중 트랙) 링크인지. 단일 트랙은 False."""
    return _spotify_kind(text) in _SPOTIFY_COLLECTION_KINDS


if __name__ == "__main__":
    print(is_spotify_url("https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8"))  # True
    print(is_spotify_url("https://open.spotify.com/intl-ko/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x"))  # True
    print(is_spotify_collection_url("https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8"))  # False
    print(is_spotify_collection_url("https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc"))  # True
    print(is_spotify_url("https://www.youtube.com/watch?v=abc"))  # False
