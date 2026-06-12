from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from cogs.music import Music, SourceNotAllowed
from core.audio.service import AudioService
from core.audio.startup_timer import PlayStartupTimer
from core.model.music_application import MusicApplication
from core.network import YoutubeSearch


class FakeTimer:
    def __init__(self) -> None:
        self.steps = []

    def mark(self, step, **fields):
        self.steps.append((step, fields))


class FakeTrack:
    def __init__(self) -> None:
        self.title = "Mapped track"
        self.uri = "https://example.test/watch?v=mapped"
        self.identifier = "mapped"
        self.length = 1234
        self.author = "Artist"
        self.thumbnail = None
        self.info = {
            "sourceName": "spotify",
            "artworkUrl": "https://example.test/art.jpg",
        }


def requester():
    return SimpleNamespace(
        id=5,
        name="requester",
        display_avatar=SimpleNamespace(url="https://example.test/avatar.png"),
    )


def application_track() -> MusicApplication:
    search = YoutubeSearch(
        audio_source="https://example.test/watch?v=track",
        title="Track",
        thumbnail_url="",
        duration=1000,
        duration_string="",
        video_id="track",
        video_url="https://example.test/watch?v=track",
        channel_id="",
        channel_url="",
        channel_name="Artist",
    )
    return MusicApplication(search, "requester", "", 5)


def make_music(backend=None):
    music = Music.__new__(Music)
    music.audio_service = SimpleNamespace(
        backend=backend or SimpleNamespace(),
        states={},
    )
    return music


def test_lavalink_mapping_preserves_original_track_and_metadata():
    original = FakeTrack()
    backend = SimpleNamespace(track_node_identifier=lambda track: "node-a")
    music = make_music(backend)

    tracks, title, count = music._lavalink_results_to_tracks(
        [original], requester(), limit=1
    )

    assert title is None
    assert count is None
    assert tracks[0].lavalink_track is original
    assert tracks[0].lavalink_node_identifier == "node-a"
    assert tracks[0].youtube_search.thumbnail_url == original.info["artworkUrl"]
    assert tracks[0].youtube_search.video_url == original.uri


@pytest.mark.parametrize(
    ("results", "expected_title", "expected_count"),
    [
        pytest.param(FakeTrack(), None, None, id="single"),
        pytest.param(
            SimpleNamespace(
                tracks=[FakeTrack(), FakeTrack()],
                playlist_info=SimpleNamespace(name="Playlist"),
            ),
            "Playlist",
            2,
            id="playlist",
        ),
    ],
)
def test_lavalink_mapping_preserves_tracks_for_all_result_shapes(
    results,
    expected_title,
    expected_count,
):
    music = make_music(SimpleNamespace())

    tracks, title, count = music._lavalink_results_to_tracks(
        results, requester(), limit=1
    )

    original = results.tracks[0] if hasattr(results, "tracks") else results
    assert tracks[0].lavalink_track is original
    assert title == expected_title
    assert count == expected_count
    assert len(tracks) == 1


@pytest.mark.asyncio
async def test_prepare_voice_and_search_runs_concurrently():
    music = make_music()
    starts = {}

    async def ensure_voice(_message):
        starts["voice"] = time.monotonic()
        await asyncio.sleep(0.1)
        return object()

    async def search(*_args, **_kwargs):
        starts["search"] = time.monotonic()
        await asyncio.sleep(0.1)
        return [application_track()], None, None

    music.ensure_voice_model = ensure_voice
    music._search_tracks = search
    message = SimpleNamespace(content="query", author=requester())

    started = time.monotonic()
    await music._prepare_voice_and_tracks(message, FakeTimer())
    elapsed = time.monotonic() - started

    assert abs(starts["voice"] - starts["search"]) < 0.03
    assert elapsed < 0.17


@pytest.mark.asyncio
async def test_search_error_cancels_voice_task():
    music = make_music()
    voice_cancelled = asyncio.Event()

    async def ensure_voice(_message):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            voice_cancelled.set()
            raise

    async def search(*_args, **_kwargs):
        await asyncio.sleep(0)
        raise SourceNotAllowed("not allowed")

    music.ensure_voice_model = ensure_voice
    music._search_tracks = search
    message = SimpleNamespace(content="query", author=requester())

    with pytest.raises(SourceNotAllowed):
        await music._prepare_voice_and_tracks(message, FakeTimer())

    assert voice_cancelled.is_set()


@pytest.mark.asyncio
async def test_on_message_keeps_background_work_off_critical_path():
    events = []
    track = application_track()
    voice_channel = SimpleNamespace(id=9)
    author = requester()
    author.bot = False
    author.voice = SimpleNamespace(channel=voice_channel)

    class FakeChannel:
        id = 77

        async def send(self, *_args, **_kwargs):
            await asyncio.sleep(0.2)
            events.append("sent")

    class FakeMessage:
        content = "query"
        guild = SimpleNamespace(id=1)
        channel = FakeChannel()

        def __init__(self):
            self.author = author

        async def delete(self, *, delay):
            await asyncio.sleep(0.2)
            events.append(("deleted", delay))

    class FakeBot:
        async def process_commands(self, _message):
            return None

    class FakeAudioService:
        states = {}

        async def enqueue_and_play(self, *_args):
            await asyncio.sleep(0.01)
            events.append("played")

    music = make_music()
    music.bot = FakeBot()
    music.audio_service = FakeAudioService()
    music.guild_channel_ids = lambda: [77]

    async def ensure_voice(_message):
        await asyncio.sleep(0.1)

    async def search(*_args, **_kwargs):
        await asyncio.sleep(0.1)
        return [track], None, None

    music.ensure_voice_model = ensure_voice
    music._search_tracks = search
    started = time.monotonic()
    await Music.on_message(music, FakeMessage())
    elapsed = time.monotonic() - started

    assert elapsed < 0.17
    assert "played" in events
    assert "sent" not in events
    assert not any(isinstance(event, tuple) and event[0] == "deleted" for event in events)

    await asyncio.sleep(0.22)
    assert "sent" in events
    assert ("deleted", 5) in events


@pytest.mark.asyncio
async def test_no_search_results_do_not_enqueue():
    voice_channel = SimpleNamespace(id=9)
    author = requester()
    author.bot = False
    author.voice = SimpleNamespace(channel=voice_channel)
    enqueue_calls = 0

    class FakeMessage:
        content = "query"
        guild = SimpleNamespace(id=1)
        channel = SimpleNamespace(id=77, send=lambda *_args, **_kwargs: asyncio.sleep(0))

        def __init__(self):
            self.author = author

        async def delete(self, *, delay):
            return None

    class FakeAudioService:
        states = {}

        async def enqueue_and_play(self, *_args):
            nonlocal enqueue_calls
            enqueue_calls += 1

    music = make_music()
    music.bot = SimpleNamespace(process_commands=lambda _message: asyncio.sleep(0))
    music.audio_service = FakeAudioService()
    music.guild_channel_ids = lambda: [77]
    music.ensure_voice_model = lambda _message: asyncio.sleep(0)

    async def no_results(*_args, **_kwargs):
        return [], None, None

    music._search_tracks = no_results

    await Music.on_message(music, FakeMessage())
    await asyncio.sleep(0)

    assert enqueue_calls == 0


@pytest.mark.asyncio
async def test_now_playing_refresh_callback_does_not_block_playback_start():
    callback_finished = asyncio.Event()

    class FakeBackend:
        async def ensure_player(self, *_args):
            return None

        async def is_playing(self, _guild_id):
            return False

        async def play(self, _guild_id, _track, _on_end):
            return None

    async def slow_refresh(_guild_id):
        await asyncio.sleep(0.2)
        callback_finished.set()

    service = AudioService(
        FakeBackend(),
        asyncio.get_running_loop(),
        on_track_start=slow_refresh,
    )
    voice_channel = SimpleNamespace(id=9)

    started = time.monotonic()
    await service.enqueue_and_play(1, voice_channel, [application_track()])
    elapsed = time.monotonic() - started

    assert elapsed < 0.05
    assert not callback_finished.is_set()

    await asyncio.wait_for(callback_finished.wait(), timeout=0.3)
