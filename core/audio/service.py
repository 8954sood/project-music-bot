from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, Dict, Iterable, Optional

import discord

from core.audio.backend import AudioBackend
from core.audio.models import AudioState, AudioStatus
from core.model.music_application import MusicApplication
from core.util import log_event
from core.config import AUDIO_BACKEND


OnGuildEvent = Callable[[int], Awaitable[None]]
MAX_TRACK_PLAYBACK_RETRIES = 1


class AudioService:
    def __init__(
        self,
        backend: AudioBackend,
        loop: asyncio.AbstractEventLoop,
        *,
        on_track_start: Optional[OnGuildEvent] = None,
        on_queue_empty: Optional[OnGuildEvent] = None,
        on_queue_changed: Optional[OnGuildEvent] = None,
    ) -> None:
        self.backend = backend
        self.loop = loop
        self.states: Dict[int, AudioState] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._play_start_times: Dict[int, float] = {}
        self._track_failure_counts: Dict[tuple[int, int], int] = {}
        self.on_track_start = on_track_start
        self.on_queue_empty = on_queue_empty
        self.on_queue_changed = on_queue_changed

    async def connect(self, bot: discord.Client) -> None:
        await self.backend.connect(bot)

    async def close(self) -> None:
        """백엔드가 등록한 백그라운드 작업 정리(cog 언로드/리로드 시)."""
        await self.backend.close()

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock

    @staticmethod
    def _consume_callback_task(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log_event(f"audio callback failed: {exc}")

    def has_state(self, guild_id: int) -> bool:
        return guild_id in self.states

    async def ensure_state(self, guild_id: int, voice_channel: discord.VoiceChannel) -> AudioState:
        await self.backend.ensure_player(guild_id, voice_channel)

        state = self.states.get(guild_id)
        if state is None or state.voice_channel_id != voice_channel.id:
            state = AudioState(
                guild_id=guild_id,
                voice_channel_id=voice_channel.id,
                now_playing=None,
                queue=[],
                volume=1.0,
                loop=False,
                is_paused=False,
            )
            self.states[guild_id] = state
        return state

    async def get_status(self, guild_id: int) -> Optional[AudioStatus]:
        state = self.states.get(guild_id)
        if state is None:
            return None
        return AudioStatus(
            now_playing=state.now_playing,
            queue=list(state.queue),
            loop=state.loop,
            is_paused=state.is_paused,
        )

    async def enqueue(self, guild_id: int, tracks: Iterable[MusicApplication]) -> None:
        state = self.states.get(guild_id)
        if state is None:
            raise RuntimeError("Audio state does not exist.")
        state.queue.extend(tracks)

    async def enqueue_and_play(
        self,
        guild_id: int,
        voice_channel: discord.VoiceChannel,
        tracks: Iterable[MusicApplication],
    ) -> None:
        queued_tracks = list(tracks)
        async with self._get_lock(guild_id):
            state = await self.ensure_state(guild_id, voice_channel)
            state.queue.extend(queued_tracks)
            should_start = state.now_playing is None
            if should_start:
                self._play_start_times[guild_id] = time.monotonic()

        if should_start:
            await self.play_next(guild_id)
        elif queued_tracks and self.on_queue_changed:
            callback_task = asyncio.create_task(self.on_queue_changed(guild_id))
            callback_task.add_done_callback(self._consume_callback_task)

    async def play_next(
        self,
        guild_id: int,
        previous: Optional[MusicApplication] = None,
        playback_error: Optional[Exception] = None,
    ) -> None:
        while True:
            queue_empty_callback = None
            next_track = None
            async with self._get_lock(guild_id):
                state = self.states.get(guild_id)
                if state is None:
                    return

                if previous is not None and playback_error is not None:
                    state.now_playing = None
                    state.is_paused = False
                    failure_key = (guild_id, id(previous))
                    failure_count = self._track_failure_counts.get(failure_key, 0) + 1
                    self._track_failure_counts[failure_key] = failure_count
                    if failure_count <= MAX_TRACK_PLAYBACK_RETRIES:
                        previous.lavalink_track = None
                        previous.lavalink_node_identifier = None
                        state.queue.insert(0, previous)
                        log_event(
                            "playback retry scheduled "
                            f"guild_id={guild_id} attempt={failure_count} error={playback_error}"
                        )
                    else:
                        self._track_failure_counts.pop(failure_key, None)
                        log_event(
                            "playback retry exhausted "
                            f"guild_id={guild_id} attempts={failure_count} error={playback_error}"
                        )
                    previous = None
                    playback_error = None
                elif state.loop and previous is not None:
                    state.queue.append(previous)
                    self._track_failure_counts.pop((guild_id, id(previous)), None)
                elif previous is not None:
                    self._track_failure_counts.pop((guild_id, id(previous)), None)

                if not state.queue:
                    state.now_playing = None
                    state.is_paused = False
                    queue_empty_callback = self.on_queue_empty
                    self._play_start_times.pop(guild_id, None)
                else:
                    next_track = state.queue.pop(0)
            if next_track is None:
                if queue_empty_callback:
                    await queue_empty_callback(guild_id)
                return

            if await self.backend.is_playing(guild_id):
                async with self._get_lock(guild_id):
                    state = self.states.get(guild_id)
                    if state is not None:
                        state.queue.insert(0, next_track)
                log_event(f"play_next skipped: already playing, re-queued track guild_id={guild_id}")
                return

            try:
                await self.backend.play(guild_id, next_track, self._on_track_end(guild_id, next_track))
                if next_track.startup_timer:
                    next_track.startup_timer.mark("playback_start")
                start_time = self._play_start_times.pop(guild_id, None)
                if start_time is not None:
                    elapsed_ms = (time.monotonic() - start_time) * 1000
                    log_event(
                        f"playback_start engine={AUDIO_BACKEND} guild_id={guild_id} elapsed_ms={elapsed_ms:.1f}"
                    )
                async with self._get_lock(guild_id):
                    state = self.states.get(guild_id)
                    if state is not None:
                        state.now_playing = next_track
                        state.is_paused = False
                if self.on_track_start:
                    if next_track.startup_timer:
                        next_track.startup_timer.mark("refresh_scheduled")
                    callback_task = asyncio.create_task(self.on_track_start(guild_id))
                    callback_task.add_done_callback(self._consume_callback_task)
                return
            except Exception as exc:
                log_event(f"play_next failed: {exc}")
                async with self._get_lock(guild_id):
                    state = self.states.get(guild_id)
                    if state is not None and state.now_playing == next_track:
                        state.now_playing = None
                        state.is_paused = False
                try:
                    if not await self.backend.is_playing(guild_id):
                        await self.backend.stop(guild_id)
                except Exception:
                    pass
                previous = None
                continue

    def _on_track_end(self, guild_id: int, previous: MusicApplication):
        def _callback(error: Optional[Exception] = None) -> None:
            asyncio.run_coroutine_threadsafe(
                self.play_next(guild_id, previous, error),
                self.loop,
            )

        return _callback

    async def pause(self, guild_id: int) -> None:
        async with self._get_lock(guild_id):
            state = self.states.get(guild_id)
            if state is None:
                return
            state.is_paused = True
        await self.backend.pause(guild_id)

    async def resume(self, guild_id: int) -> None:
        async with self._get_lock(guild_id):
            state = self.states.get(guild_id)
            if state is None:
                return
            state.is_paused = False
        await self.backend.resume(guild_id)

    async def stop(self, guild_id: int) -> None:
        async with self._get_lock(guild_id):
            state = self.states.get(guild_id)
            if state is None:
                return
            state.queue.clear()
            state.now_playing = None
            state.is_paused = False
            self._play_start_times.pop(guild_id, None)
            for key in [key for key in self._track_failure_counts if key[0] == guild_id]:
                self._track_failure_counts.pop(key, None)
        await self.backend.stop(guild_id)

    async def skip(self, guild_id: int) -> None:
        await self.backend.skip(guild_id)

    async def disconnect(self, guild_id: int) -> None:
        async with self._get_lock(guild_id):
            self.states.pop(guild_id, None)
            self._play_start_times.pop(guild_id, None)
            for key in [key for key in self._track_failure_counts if key[0] == guild_id]:
                self._track_failure_counts.pop(key, None)
        await self.backend.disconnect(guild_id)

    async def toggle_loop(self, guild_id: int) -> bool:
        async with self._get_lock(guild_id):
            state = self.states.get(guild_id)
            if state is None:
                return False
            state.loop = not state.loop
            return state.loop

    async def shuffle(self, guild_id: int) -> None:
        async with self._get_lock(guild_id):
            state = self.states.get(guild_id)
            if state is None:
                return
            random.shuffle(state.queue)
