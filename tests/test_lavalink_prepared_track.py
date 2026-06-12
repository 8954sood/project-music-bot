from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.audio.lavalink_node_backend import LavalinkNodeBackend
from core.audio.lavalink_node_pool import NodeInfo
from core.model.music_application import MusicApplication
from core.network import YoutubeSearch


class FakePool:
    def __init__(self, node: NodeInfo) -> None:
        self.node = node

    async def run_with_failover(self, action):
        return await action(self.node)


class FakeNodePool:
    target = None

    @classmethod
    def get_node(cls, *, identifier):
        assert identifier == cls.target._identifier
        return cls.target


class FakePlayer:
    def __init__(
        self,
        node,
        *,
        fail_prepared: bool = False,
        fail_direct_load: bool = False,
    ) -> None:
        self.node = node
        self.is_connected = True
        self.fail_prepared = fail_prepared
        self.fail_direct_load = fail_direct_load
        self.get_tracks_call_count = 0
        self.get_tracks_query = None
        self.play_calls = []
        self.loaded_track = object()

    async def get_tracks(self, *, query):
        self.get_tracks_call_count += 1
        self.get_tracks_query = query
        if self.fail_direct_load:
            raise RuntimeError("direct load failed")
        return [self.loaded_track]

    async def play(self, *, track):
        self.play_calls.append(track)
        if self.fail_prepared and len(self.play_calls) == 1:
            raise RuntimeError("prepared track rejected")


def make_track(prepared=None, node_identifier=None) -> MusicApplication:
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
    return MusicApplication(
        youtube_search=search,
        user_name="requester",
        user_icon="",
        user_id=1,
        lavalink_track=prepared,
        lavalink_node_identifier=node_identifier,
    )


def make_backend(player):
    node_info = NodeInfo(
        unique_id="node-a",
        identifier="node-a",
        host="localhost",
        port=2333,
        password="",
        secure=False,
        version="v4",
    )
    backend = LavalinkNodeBackend.__new__(LavalinkNodeBackend)
    backend._players = {1: player}
    backend._pool = FakePool(node_info)
    backend._pomice = SimpleNamespace(NodePool=FakeNodePool)
    backend._monitor_tasks = {}
    backend._start_monitor = lambda *args: None
    return backend


@pytest.mark.asyncio
async def test_prepared_track_skips_direct_load():
    target = SimpleNamespace(_identifier="node-a")
    FakeNodePool.target = target
    player = FakePlayer(target)
    backend = make_backend(player)
    prepared = object()

    await backend.play(1, make_track(prepared, "node-a"), lambda _: None)

    assert player.get_tracks_call_count == 0
    assert player.play_calls == [prepared]


@pytest.mark.asyncio
async def test_missing_prepared_track_uses_direct_load():
    target = SimpleNamespace(_identifier="node-a")
    FakeNodePool.target = target
    player = FakePlayer(target)
    backend = make_backend(player)
    track = make_track()

    await backend.play(1, track, lambda _: None)

    assert player.get_tracks_call_count == 1
    assert player.get_tracks_query == track.youtube_search.video_url
    assert player.play_calls == [player.loaded_track]


@pytest.mark.asyncio
async def test_prepared_track_failure_falls_back_once():
    target = SimpleNamespace(_identifier="node-a")
    FakeNodePool.target = target
    player = FakePlayer(target, fail_prepared=True)
    backend = make_backend(player)
    prepared = object()

    await backend.play(1, make_track(prepared, "node-a"), lambda _: None)

    assert player.get_tracks_call_count == 1
    assert player.play_calls == [prepared, player.loaded_track]


@pytest.mark.asyncio
async def test_prepared_track_from_other_node_uses_direct_load():
    target = SimpleNamespace(_identifier="node-a")
    FakeNodePool.target = target
    player = FakePlayer(target)
    backend = make_backend(player)
    prepared = object()

    await backend.play(1, make_track(prepared, "node-b"), lambda _: None)

    assert player.get_tracks_call_count == 1
    assert player.play_calls == [player.loaded_track]


@pytest.mark.asyncio
async def test_failed_prepared_and_direct_load_propagates_to_failover_pool():
    target = SimpleNamespace(_identifier="node-a")
    FakeNodePool.target = target
    player = FakePlayer(target, fail_prepared=True, fail_direct_load=True)
    backend = make_backend(player)

    with pytest.raises(RuntimeError, match="direct load failed"):
        await backend.play(1, make_track(object(), "node-a"), lambda _: None)

    assert player.get_tracks_call_count == 1
