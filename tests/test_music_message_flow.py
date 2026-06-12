from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.music as music_module
from cogs.music import (
    Music,
    SourceNotAllowed,
    TrackSearchFailed,
    VoicePreparationFailed,
)
from core.audio.lavalink_node_backend import LavalinkTrackResults
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
    music = make_music()

    tracks, title, count = music._lavalink_results_to_tracks(
        [original], requester(), limit=1, node_identifier="node-a"
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
    voice_started = asyncio.Event()
    search_started = asyncio.Event()
    release = asyncio.Event()

    async def ensure_voice(_message):
        voice_started.set()
        await release.wait()
        return object()

    async def search(*_args, **_kwargs):
        search_started.set()
        await release.wait()
        return [application_track()], None, None

    music.ensure_voice_model = ensure_voice
    music._search_tracks = search
    message = SimpleNamespace(content="query", author=requester())

    task = asyncio.create_task(music._prepare_voice_and_tracks(message, FakeTimer()))
    await asyncio.wait_for(voice_started.wait(), timeout=0.1)
    await asyncio.wait_for(search_started.wait(), timeout=0.1)
    assert not task.done()

    release.set()
    await task


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
    release_background = asyncio.Event()
    track = application_track()
    voice_channel = SimpleNamespace(id=9)
    author = requester()
    author.bot = False
    author.voice = SimpleNamespace(channel=voice_channel)

    class FakeChannel:
        id = 77

        async def send(self, *_args, **_kwargs):
            await release_background.wait()
            events.append("sent")

    class FakeMessage:
        content = "query"
        guild = SimpleNamespace(id=1)
        channel = FakeChannel()

        def __init__(self):
            self.author = author

        async def delete(self, *, delay):
            await release_background.wait()
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
    await Music.on_message(music, FakeMessage())

    assert "played" in events
    assert "sent" not in events
    assert not any(isinstance(event, tuple) and event[0] == "deleted" for event in events)

    release_background.set()
    await asyncio.sleep(0)
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
async def test_lavalink_node_search_failure_is_not_treated_as_empty_results():
    backend = SimpleNamespace(
        get_tracks=AsyncMock(side_effect=RuntimeError("all clients failed"))
    )
    music = make_music(backend)

    with pytest.raises(TrackSearchFailed):
        await music._search_tracks_lavalink_node("query", requester(), limit=1)


@pytest.mark.asyncio
async def test_lavalink_node_search_result_carries_node_identifier():
    original = FakeTrack()
    backend = SimpleNamespace(
        get_tracks=AsyncMock(
            return_value=LavalinkTrackResults([original], "node-a")
        )
    )
    music = make_music(backend)

    tracks, _, _ = await music._search_tracks_lavalink_node(
        "query",
        requester(),
        limit=1,
    )

    assert tracks[0].lavalink_track is original
    assert tracks[0].lavalink_node_identifier == "node-a"


@pytest.mark.asyncio
async def test_lavalink_search_failure_is_not_treated_as_empty_results():
    backend = SimpleNamespace(
        _node=SimpleNamespace(
            get_tracks=AsyncMock(side_effect=RuntimeError("all clients failed"))
        )
    )
    music = make_music(backend)

    with pytest.raises(TrackSearchFailed):
        await music._search_tracks_lavalink("query", requester(), limit=1)


@pytest.mark.asyncio
async def test_on_message_reports_unknown_error_for_search_failure():
    sent_messages = []
    voice_channel = SimpleNamespace(id=9)
    author = requester()
    author.bot = False
    author.voice = SimpleNamespace(channel=voice_channel)

    class FakeChannel:
        id = 77

        async def send(self, content, **_kwargs):
            sent_messages.append(content)

    class FakeMessage:
        content = "query"
        guild = SimpleNamespace(id=1)
        channel = FakeChannel()

        def __init__(self):
            self.author = author

        async def delete(self, *, delay):
            return None

    class FakeAudioService:
        states = {}

        async def enqueue_and_play(self, *_args):
            raise AssertionError("search failure must not enqueue")

    music = make_music()
    music.bot = SimpleNamespace(process_commands=lambda _message: asyncio.sleep(0))
    music.audio_service = FakeAudioService()
    music.guild_channel_ids = lambda: [77]
    music.ensure_voice_model = lambda _message: asyncio.sleep(0)

    async def failed_search(*_args, **_kwargs):
        raise TrackSearchFailed

    music._search_tracks = failed_search

    await Music.on_message(music, FakeMessage())
    await asyncio.sleep(0)

    assert sent_messages == [
        "알 수 없는 오류로 노래를 검색하지 못했어요. 잠시 후 다시 시도해 주세요."
    ]


@pytest.mark.asyncio
async def test_on_message_reports_unknown_error_for_voice_failure():
    sent_messages = []
    voice_channel = SimpleNamespace(id=9)
    author = requester()
    author.bot = False
    author.voice = SimpleNamespace(channel=voice_channel)

    class FakeChannel:
        id = 77

        async def send(self, content, **_kwargs):
            sent_messages.append(content)

    class FakeMessage:
        content = "query"
        guild = SimpleNamespace(id=1)
        channel = FakeChannel()

        def __init__(self):
            self.author = author

        async def delete(self, *, delay):
            return None

    music = make_music()
    music.bot = SimpleNamespace(process_commands=lambda _message: asyncio.sleep(0))
    music.audio_service = SimpleNamespace(states={})
    music.guild_channel_ids = lambda: [77]

    async def failed_voice(_message):
        raise RuntimeError("voice connect failed")

    async def search(*_args, **_kwargs):
        await asyncio.sleep(10)

    music.ensure_voice_model = failed_voice
    music._search_tracks = search

    await Music.on_message(music, FakeMessage())
    await asyncio.sleep(0)

    assert sent_messages == [
        "알 수 없는 오류로 음성 채널에 연결하지 못했어요. 잠시 후 다시 시도해 주세요."
    ]


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

    await service.enqueue_and_play(1, voice_channel, [application_track()])

    assert not callback_finished.is_set()

    await asyncio.wait_for(callback_finished.wait(), timeout=0.3)


@pytest.mark.asyncio
async def test_enqueue_while_playing_schedules_queue_view_refresh():
    queue_changed = asyncio.Event()

    class FakeBackend:
        async def ensure_player(self, *_args):
            return None

        async def is_playing(self, _guild_id):
            return False

        async def play(self, _guild_id, _track, _on_end):
            return None

    async def on_queue_changed(_guild_id):
        queue_changed.set()

    service = AudioService(
        FakeBackend(),
        asyncio.get_running_loop(),
        on_queue_changed=on_queue_changed,
    )
    voice_channel = SimpleNamespace(id=9)
    first = application_track()
    second = application_track()
    second.youtube_search.title = "Second"

    await service.enqueue_and_play(1, voice_channel, [first])
    await service.enqueue_and_play(1, voice_channel, [second])
    await asyncio.wait_for(queue_changed.wait(), timeout=0.1)

    status = await service.get_status(1)
    assert status is not None
    assert status.now_playing is first
    assert status.queue == [second]


@pytest.mark.asyncio
async def test_refresh_now_playing_embed_includes_queue_titles(monkeypatch):
    now_playing = application_track()
    queued = application_track()
    queued.youtube_search.title = "Queued song"
    captured = {}

    def fake_build_now_playing_view(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        music_module,
        "build_now_playing_view",
        fake_build_now_playing_view,
    )
    music = make_music()
    music.audio_service = SimpleNamespace(
        get_status=AsyncMock(
            return_value=SimpleNamespace(
                now_playing=now_playing,
                queue=[queued],
                loop=False,
                is_paused=False,
            )
        )
    )
    music.music_message_edit = AsyncMock()

    await music.refresh_now_playing_embed(1)

    assert captured["queue_preview"] == "1. Queued song"
    music.music_message_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_track_retries_once_with_direct_load_then_advances_queue():
    class FakeBackend:
        def __init__(self):
            self.play_calls = []
            self.callbacks = []

        async def ensure_player(self, *_args):
            return None

        async def is_playing(self, _guild_id):
            return False

        async def play(self, _guild_id, track, on_end):
            self.play_calls.append(track)
            self.callbacks.append(on_end)

        async def stop(self, _guild_id):
            return None

    backend = FakeBackend()
    service = AudioService(backend, asyncio.get_running_loop())
    voice_channel = SimpleNamespace(id=9)
    first = application_track()
    second = application_track()
    second.youtube_search.title = "Second"
    second.lavalink_track = object()
    second.lavalink_node_identifier = "node-a"
    third = application_track()
    third.youtube_search.title = "Third"

    await service.enqueue_and_play(1, voice_channel, [first, second, third])
    await service.play_next(1, first)
    assert backend.play_calls[-1] is second

    await service.play_next(1, second, RuntimeError("stream timeout"))
    assert backend.play_calls[-1] is second
    assert second.lavalink_track is None
    assert second.lavalink_node_identifier is None

    await service.play_next(1, second, RuntimeError("stream timeout again"))
    assert backend.play_calls[-1] is third
    assert backend.play_calls == [first, second, second, third]
