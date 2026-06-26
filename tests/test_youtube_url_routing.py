from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.music import Music
from core.audio.lavalink_node_backend import LavalinkTrackResults
from core.network.youtube.internal.youtube_utile import is_playlist_url, is_youtube_url


class FakeTrack:
    def __init__(self, identifier: str, title: str = "Direct title") -> None:
        self.title = title
        self.uri = f"https://www.youtube.com/watch?v={identifier}"
        self.identifier = identifier
        self.length = 123456
        self.author = "Channel"
        self.thumbnail = None
        self.info = {
            "sourceName": "youtube",
            "artworkUrl": f"https://i.ytimg.com/vi/{identifier}/hqdefault.jpg",
        }


def requester():
    return SimpleNamespace(
        id=5,
        name="requester",
        display_avatar=SimpleNamespace(url="https://example.test/avatar.png"),
    )


def make_music(backend):
    music = Music.__new__(Music)
    music.audio_service = SimpleNamespace(backend=backend, states={})
    return music


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=siNFnlqtd8M",
        "https://youtu.be/siNFnlqtd8M",
        "https://www.youtube.com/playlist?list=PLUQKJP1sVuNPMJad6pUdbjp2vvU2hGIAp",
        "https://youtube.com/playlist?list=PLUQKJP1sVuNPMJad6pUdbjp2vvU2hGIAp",
    ],
)
def test_youtube_urls_are_detected_as_direct_urls(url):
    assert is_youtube_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PLUQKJP1sVuNPMJad6pUdbjp2vvU2hGIAp",
        "https://youtube.com/playlist?list=PLUQKJP1sVuNPMJad6pUdbjp2vvU2hGIAp",
    ],
)
def test_playlist_urls_are_detected_without_dropping_list_parameter(url):
    assert is_playlist_url(url)


@pytest.mark.asyncio
async def test_watch_url_is_passed_to_lavalink_without_ytsearch():
    url = "https://www.youtube.com/watch?v=siNFnlqtd8M"
    backend = SimpleNamespace(
        get_tracks=AsyncMock(
            return_value=LavalinkTrackResults([FakeTrack("siNFnlqtd8M")], "node-a")
        )
    )
    music = make_music(backend)

    tracks, playlist_title, playlist_count = await music._search_tracks_lavalink_node(
        url, requester(), limit=1
    )

    backend.get_tracks.assert_awaited_once_with(url)
    assert playlist_title is None
    assert playlist_count is None
    assert tracks[0].youtube_search.title == "Direct title"
    assert tracks[0].lavalink_track.identifier == "siNFnlqtd8M"


@pytest.mark.asyncio
async def test_playlist_url_is_passed_to_lavalink_without_ytsearch_and_keeps_all_tracks():
    url = "https://www.youtube.com/playlist?list=PLUQKJP1sVuNPMJad6pUdbjp2vvU2hGIAp"
    originals = [FakeTrack("first", "First"), FakeTrack("second", "Second")]
    playlist = SimpleNamespace(
        tracks=originals,
        playlist_info=SimpleNamespace(name="Playlist title"),
    )
    backend = SimpleNamespace(
        get_tracks=AsyncMock(return_value=LavalinkTrackResults(playlist, "node-a"))
    )
    music = make_music(backend)

    tracks, playlist_title, playlist_count = await music._search_tracks_lavalink_node(
        url, requester(), limit=1
    )

    backend.get_tracks.assert_awaited_once_with(url)
    assert playlist_title == "Playlist title"
    assert playlist_count == 2
    assert [track.youtube_search.title for track in tracks] == ["First", "Second"]
    assert [track.lavalink_track for track in tracks] == originals
    assert all(track.lavalink_node_identifier == "node-a" for track in tracks)


def test_lavalink_playlist_mapping_distinguishes_empty_playlist_from_load_failure():
    playlist = SimpleNamespace(
        tracks=[],
        playlist_info=SimpleNamespace(name="Empty playlist"),
    )
    music = make_music(SimpleNamespace())

    tracks, playlist_title, playlist_count = music._lavalink_results_to_tracks(
        playlist, requester(), limit=None
    )

    assert tracks == []
    assert playlist_title == "Empty playlist"
    assert playlist_count == 0


def test_lavalink_v4_playlist_dict_mapping_preserves_info_and_original_tracks():
    original = {
        "info": {
            "title": "又三郎",
            "author": "ヨルシカ / n-buna Official",
            "identifier": "siNFnlqtd8M",
            "uri": "https://www.youtube.com/watch?v=siNFnlqtd8M",
            "length": 230000,
            "artworkUrl": "https://i.ytimg.com/vi/siNFnlqtd8M/mqdefault.jpg",
            "sourceName": "youtube",
        }
    }
    results = {
        "loadType": "playlist",
        "data": {
            "info": {"name": "幻燈", "selectedTrack": -1},
            "tracks": [original],
        },
    }
    music = make_music(SimpleNamespace())

    tracks, playlist_title, playlist_count = music._lavalink_results_to_tracks(
        results, requester(), limit=None
    )

    assert playlist_title == "幻燈"
    assert playlist_count == 1
    assert tracks[0].lavalink_track is original
    assert tracks[0].youtube_search.title == "又三郎"
    assert tracks[0].youtube_search.thumbnail_url.endswith("/mqdefault.jpg")
