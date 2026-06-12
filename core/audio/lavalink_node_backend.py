"""lavalink-node 엔진 백엔드 (pomice 기반): 공개 노드 풀 + 순서기반 sticky failover.

노드 탐색/판별 + sticky failover 로직은 LavalinkNodePool(discord/pomice 무의존)이 담당하고,
이 백엔드는 "선택된 노드로 실제 재생" 동작(action)을 pomice 로 채운다.

failover 동작
-------------
- 풀의 run_with_failover(action) 가 sticky 노드부터 순서대로 action 을 호출한다.
- action(node) 은 단일 pomice.Player 를 해당 노드로 이동(_swap_to)시킨 뒤 재생을 시도한다.
- 성공 노드가 sticky 로 고정되고, 실패 노드는 풀에서 제거된다(다음 재생은 sticky 노드부터).
- 재생 중 노드 장애는 play 시점 failover 로만 처리한다(곡 중간 이어재생 X).

pomice 사용 주의
----------------
- pomice.Player 가 곧 discord.VoiceProtocol 이라 별도 음성 브리지가 필요 없다.
- 길드당 음성 연결은 하나뿐이라 Player 도 하나다. failover 는 그 Player 를 노드 간 이동시키는 방식.
- pomice 의 Player._swap_node 는 재생 중(current 존재)이 아닐 때 내부 변수(data) 미할당으로 크래시하는
  버그가 있어, 트랙 없는 상태에서도 안전하게 노드를 옮기는 _swap_to 를 자체 구현한다(아래 주석 참고).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional, Set

import discord

from core.audio.backend import AudioBackend, OnTrackEnd
from core.audio.lavalink_node_pool import LavalinkNodePool, NodeInfo
from core.config import (
    LAVALINK_NODE_PROBE_TIMEOUT,
    LAVALINK_NODE_REFRESH_HOURS,
    LAVALINK_NODE_SOURCE,
)
from core.model.music_application import MusicApplication
from core.util import log_event, log_exception


@dataclass(frozen=True)
class LavalinkTrackResults:
    payload: object
    node_identifier: str


class LavalinkNodeBackend(AudioBackend):
    def __init__(self) -> None:
        try:
            import pomice
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Pomice is required for the lavalink-node backend.") from exc

        self._pomice = pomice
        self._pool = LavalinkNodePool()
        self._bot: Optional[discord.Client] = None
        self._players: Dict[int, "pomice.Player"] = {}
        self._end_callbacks: Dict[int, OnTrackEnd] = {}
        self._track_errors: Dict[int, Exception] = {}
        self._listeners_registered = False
        self._created: Set[str] = set()  # create_node 성공한 노드 identifier
        self._register_lock = asyncio.Lock()  # discover/등록 동시 실행 직렬화
        self._refresh_task: Optional[asyncio.Task] = None  # 주기적 노드 재탐색 루프

    # ------------------------------------------------------------------ #
    # 수명주기
    # ------------------------------------------------------------------ #
    async def connect(self, bot: discord.Client) -> None:
        self._bot = bot
        self._register_event_listeners()
        async with self._register_lock:
            await self._register_pool_nodes()
        # 공개 노드는 수시로 죽고 살아나므로 주기적으로(기본 24h) 재탐색한다. 시작 시 1회만 띄움.
        if self._refresh_task is None and LAVALINK_NODE_REFRESH_HOURS > 0:
            self._refresh_task = asyncio.create_task(self._periodic_refresh_loop())

    async def close(self) -> None:
        """정기 재탐색 task와 pomice track event listener를 정리한다.

        cog 언로드/리로드 시 이전 backend의 listener가 새 인스턴스와 중복 실행되는 것을 막는다.
        음성 연결(player) 자체는 건드리지 않는다.
        """
        task = self._refresh_task
        self._refresh_task = None
        if task is not None:
            task.cancel()

        self._remove_event_listeners()
        self._end_callbacks.clear()
        self._track_errors.clear()

    def _register_event_listeners(self) -> None:
        if self._bot is None or self._listeners_registered:
            return
        add_listener = getattr(self._bot, "add_listener", None)
        if not callable(add_listener):
            return
        add_listener(self._on_pomice_track_exception, "on_pomice_track_exception")
        add_listener(self._on_pomice_track_stuck, "on_pomice_track_stuck")
        add_listener(self._on_pomice_track_end, "on_pomice_track_end")
        self._listeners_registered = True

    def _remove_event_listeners(self) -> None:
        if self._bot is None or not self._listeners_registered:
            return
        remove_listener = getattr(self._bot, "remove_listener", None)
        if callable(remove_listener):
            remove_listener(self._on_pomice_track_exception, "on_pomice_track_exception")
            remove_listener(self._on_pomice_track_stuck, "on_pomice_track_stuck")
            remove_listener(self._on_pomice_track_end, "on_pomice_track_end")
        self._listeners_registered = False

    def _event_guild_id(self, player) -> Optional[int]:
        guild = getattr(player, "guild", None)
        guild_id = getattr(guild, "id", None)
        if guild_id is None or self._players.get(guild_id) is not player:
            return None
        return guild_id

    async def _on_pomice_track_exception(self, player, _track, error) -> None:
        guild_id = self._event_guild_id(player)
        if guild_id is None:
            return
        self._track_errors[guild_id] = RuntimeError(f"Lavalink track exception: {error}")
        node_id = getattr(getattr(player, "node", None), "_identifier", "unknown")
        track_id = getattr(_track, "identifier", None) or getattr(_track, "track_id", "unknown")
        log_event(
            "lavalink-node track exception "
            f"guild_id={guild_id} node={node_id} track_id={track_id} error={error}"
        )

    async def _on_pomice_track_stuck(self, player, _track, threshold) -> None:
        guild_id = self._event_guild_id(player)
        if guild_id is None:
            return
        self._track_errors[guild_id] = RuntimeError(
            f"Lavalink track stuck after {threshold}ms"
        )
        node_id = getattr(getattr(player, "node", None), "_identifier", "unknown")
        track_id = getattr(_track, "identifier", None) or getattr(_track, "track_id", "unknown")
        log_event(
            "lavalink-node track stuck "
            f"guild_id={guild_id} node={node_id} track_id={track_id} "
            f"threshold_ms={threshold}"
        )

    async def _on_pomice_track_end(self, player, _track, reason) -> None:
        guild_id = self._event_guild_id(player)
        if guild_id is None:
            return
        normalized_reason = str(reason).lower()
        if normalized_reason == "replaced":
            self._track_errors.pop(guild_id, None)
            node_id = getattr(getattr(player, "node", None), "_identifier", "unknown")
            track_id = getattr(_track, "identifier", None) or getattr(
                _track, "track_id", "unknown"
            )
            log_event(
                "lavalink-node track end "
                f"guild_id={guild_id} node={node_id} track_id={track_id} "
                f"reason={reason} ignored=true"
            )
            return

        error = self._track_errors.pop(guild_id, None)
        if error is None and normalized_reason in {"loadfailed", "load_failed", "cleanup"}:
            error = RuntimeError(f"Lavalink track ended with reason={reason}")

        callback = self._end_callbacks.pop(guild_id, None)
        node_id = getattr(getattr(player, "node", None), "_identifier", "unknown")
        track_id = getattr(_track, "identifier", None) or getattr(_track, "track_id", "unknown")
        log_event(
            "lavalink-node track end "
            f"guild_id={guild_id} node={node_id} track_id={track_id} reason={reason} "
            f"has_error={error is not None} callback_registered={callback is not None}"
        )
        if callback is not None:
            callback(error)

    def _in_use(self) -> bool:
        """어느 길드든 음성에 연결된 player 가 있으면 '사용 중'으로 본다(재탐색은 player 를 파괴하므로)."""
        for player in self._players.values():
            try:
                if self._is_connected(player):
                    return True
            except Exception:
                pass
        return False

    async def _periodic_refresh_loop(self) -> None:
        """LAVALINK_NODE_REFRESH_HOURS 마다 노드 풀을 재탐색한다.

        단, 재탐색은 모든 player 를 파괴(재생 중단)하므로 '사용 중'인 주기는 건너뛰고 다음 주기로 미룬다.
        """
        interval = LAVALINK_NODE_REFRESH_HOURS * 3600
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            if self._in_use():
                log_event("lavalink-node: periodic refresh skipped (in use)")
                continue
            try:
                healthy = await self.refresh_nodes()
                log_event(f"lavalink-node: periodic refresh done, {len(healthy)} healthy nodes")
            except Exception as exc:
                log_exception("lavalink-node periodic refresh failed", exc)

    def _available_nodes(self):
        return [n for n in self._pomice.NodePool._nodes.values() if getattr(n, "_available", False)]

    async def _ensure_nodes_ready(self) -> None:
        """pomice 노드가 하나도 없으면 (재)등록. 시작 직후 discover 완료 전에 재생 요청이 와도
        NoNodesAvailable 로 죽지 않도록 한다."""
        if self._available_nodes():
            return
        async with self._register_lock:
            if self._available_nodes():  # 대기 중 다른 코루틴이 등록했을 수 있음
                return
            await self._register_pool_nodes()

    async def _register_pool_nodes(self) -> None:
        """노드 목록을 받아 각 노드의 (YouTube 판별 + /version 연결 + ready)를 **하나의 파이프라인**으로
        묶어 모든 노드를 동시에 처리한다.

        probe 와 register 를 노드별로 합쳐 한 번에 돌리고, **전체를 하나의 데드라인
        (LAVALINK_NODE_PROBE_TIMEOUT) 안에서** 수행한다. 좋은 노드는 probe+register 가 빨라 그 안에
        끝나고, 느린/죽은 노드는 데드라인에서 잘려 제외된다(probe 와 register 가 각각 별도 타임아웃을
        먹어 합산되던 문제를 없앤다).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + LAVALINK_NODE_PROBE_TIMEOUT

        nodes = await self._pool.fetch_nodes(timeout=max(0.1, deadline - loop.time()))
        results = await asyncio.gather(
            *(self._check_and_register(node, deadline) for node in nodes),
            return_exceptions=True,
        )
        self._created = {nid for nid in results if isinstance(nid, str)}

        # YouTube 판별 + 연결까지 통과한 노드만 failover 대상으로.
        self._pool.set_healthy([n for n in nodes if n.identifier in self._created])
        log_event(
            f"lavalink-node[{LAVALINK_NODE_SOURCE}]: "
            f"{len(self._created)}/{len(nodes)} pomice nodes connected"
        )

    async def _check_and_register(self, node: NodeInfo, deadline: float) -> Optional[str]:
        """노드 1개: YouTube 판별 → /version 연결 → ready 대기 → 버전보정. 성공 시 identifier.

        모든 네트워크 단계가 공유 deadline 까지 남은 시간으로 제한된다(probe+register 총합이
        LAVALINK_NODE_PROBE_TIMEOUT 을 넘지 않음).
        """
        from pomice.utils import LavalinkVersion

        loop = asyncio.get_event_loop()
        remaining = lambda: max(0.1, deadline - loop.time())  # noqa: E731

        # 1) 재생 가능 판별 (LAVALINK_NODE_SOURCE 모드별: youtube / spotify / both)
        try:
            ok = await self._pool.probe_source(node, timeout=remaining())
        except Exception:
            ok = False
        if not ok:
            return None

        if node.identifier in self._pomice.NodePool._nodes:
            return node.identifier

        # 2) /version 검증 + WS 연결
        try:
            pnode = await asyncio.wait_for(
                self._pomice.NodePool.create_node(
                    bot=self._bot,
                    host=node.host,
                    port=node.port,
                    password=node.password,
                    identifier=node.identifier,
                    secure=node.secure,
                ),
                timeout=remaining(),
            )
        except Exception as exc:
            # /version 실패/타임아웃/연결 실패 노드는 수용하지 않는다(스킵).
            log_exception(f"create_node failed node={node.label}", exc)
            self._pomice.NodePool._nodes.pop(node.identifier, None)  # 타임아웃 시 잔여분 정리
            return None

        # 3) WS ready(op) 로 session_id 가 잡힐 때까지 대기(없으면 Player endpoint uri 가
        #    sessions/None → 404 Session not found). 남은 데드라인까지만 기다린다.
        if not await self._await_session(pnode, timeout=remaining()):
            log_event(f"node {node.label} ready timeout (no session id) -> skip")
            try:
                await pnode.disconnect()
            except Exception:
                pass
            self._pomice.NodePool._nodes.pop(node.identifier, None)
            return None

        # 4) Lavalink 4.2+ 는 voice payload 에 channelId 가 필수. SNAPSHOT 등으로 4.2 미만으로 잡히면
        #    channelId 가 빠져 voice PATCH 가 400 나므로 버전을 올린다(구버전은 무시 → 안전).
        try:
            if pnode._version < LavalinkVersion(4, 2, 0):
                pnode._version = LavalinkVersion(4, 4, 0)
        except Exception:
            pass
        return node.identifier

    async def _await_session(self, pnode, *, timeout: float = 8.0) -> bool:
        """pomice 노드가 WS ready 로 session_id 를 받을 때까지 대기. 받으면 True."""
        waited = 0.0
        while waited < timeout:
            if getattr(pnode, "_session_id", None):
                return True
            await asyncio.sleep(0.1)
            waited += 0.1
        return bool(getattr(pnode, "_session_id", None))

    # ------------------------------------------------------------------ #
    # 노드 이동 (안전한 swap)
    # ------------------------------------------------------------------ #
    async def _swap_to(self, player, new_node) -> None:
        """player 를 new_node 로 이동. pomice Player._swap_node 의 트랙없음 크래시 버그를 회피한 버전.

        ⚠️ pomice 의 semi-private 내부(_node/_players/_refresh_endpoint_uri/_dispatch_voice_update/
        _player_endpoint_uri/node.send)에 의존한다. pomice 버전 업 시 점검 필요.
        """
        old = player.node
        if old is new_node:
            return

        data = None
        if player.current:
            data = {"position": player.position, "encodedTrack": player.current.track_id}

        guild_id = player.guild.id
        if guild_id in old._players:
            del old._players[guild_id]
        player._node = new_node
        new_node._players[guild_id] = player

        await player._refresh_endpoint_uri(new_node._session_id)
        await player._dispatch_voice_update()  # 새 노드로 voice 자격증명 재전송
        if data is not None:
            # 재생 중이었으면 현재 트랙/위치를 새 노드에서 이어재생.
            await new_node.send(
                method="PATCH",
                path=player._player_endpoint_uri,
                guild_id=guild_id,
                data=data,
            )
        log_event(f"swapped player guild={guild_id} -> node={new_node._identifier}")

    # ------------------------------------------------------------------ #
    # AudioBackend 구현
    # ------------------------------------------------------------------ #
    def _is_connected(self, player) -> bool:
        value = player.is_connected
        return value() if callable(value) else bool(value)

    async def ensure_player(self, guild_id: int, voice_channel: discord.VoiceChannel) -> None:
        # 재생 요청이 시작 직후(노드 등록 전)에 와도 죽지 않도록 노드 준비 보장.
        await self._ensure_nodes_ready()

        # guild.voice_client(=pomice.Player) 를 단일 진실원으로 삼는다(self._players 가 stale 일 수 있음).
        guild = voice_channel.guild
        player = guild.voice_client
        if player is not None:
            same_channel = (
                self._is_connected(player)
                and getattr(player, "channel", None) is not None
                and player.channel.id == voice_channel.id
            )
            if same_channel:
                log_event(f"ensure_player reuse guild={guild_id} channel={voice_channel.id}")
                self._players[guild_id] = player
                return
            # 다른 채널이거나 stale 상태 → 깔끔히 끊고 새로 연결("Already connected" 방지).
            log_event(f"ensure_player reconnect guild={guild_id} channel={voice_channel.id}")
            try:
                await player.disconnect(force=True)
            except Exception:
                pass
            self._players.pop(guild_id, None)

        try:
            player = await voice_channel.connect(cls=self._pomice.Player)
        except Exception as exc:
            log_exception(
                f"ensure_player connect failed guild_id={guild_id} channel_id={voice_channel.id}",
                exc,
            )
            raise
        log_event(f"ensure_player connected guild={guild_id} channel={voice_channel.id} node={getattr(player.node, '_identifier', None)}")
        self._players[guild_id] = player

    async def get_tracks(self, query: str):
        """검색용 loadtracks 결과와 성공 노드 identifier를 함께 반환한다.

        - 결과 없음(loadType empty)은 노드 실패가 아니라 정상 응답이므로 그대로(None/빈 결과) 반환한다.
          (그렇지 않으면 매칭 없는 검색어 하나가 멀쩡한 노드를 전부 제거해버린다.)
        - 노드가 에러를 던지면(get_tracks 예외) failover 로 다음 노드를 시도한다.
        - 사용 가능한 노드가 없거나 전부 실패하면 RuntimeError (호출부에서 빈 결과로 처리).
        """
        async def action(node: NodeInfo):
            target = self._pomice.NodePool.get_node(identifier=node.identifier)
            results = await target.get_tracks(query=query)
            return LavalinkTrackResults(results, node.identifier)

        return await self._pool.run_with_failover(action)

    @staticmethod
    def _result_tracks(results):
        if results is None:
            return []
        if isinstance(results, list):
            return results
        if hasattr(results, "tracks"):
            return list(results.tracks)
        return [results]

    async def play(self, guild_id: int, track: MusicApplication, on_end: OnTrackEnd) -> None:
        player = self._players.get(guild_id)
        if player is None or not self._is_connected(player):
            raise RuntimeError("Lavalink player is not connected.")

        async def action(node: NodeInfo):
            target = self._pomice.NodePool.get_node(identifier=node.identifier)  # 없으면 예외→failover
            if player.node is None or player.node._identifier != node.identifier:
                await self._swap_to(player, target)

            timer = track.startup_timer
            prepared = track.lavalink_track
            prepared_node = track.lavalink_node_identifier
            same_node = prepared_node is None or prepared_node == node.identifier
            fallback_reason = None

            if prepared is not None and same_node:
                self._end_callbacks[guild_id] = on_end
                self._track_errors.pop(guild_id, None)
                try:
                    if timer:
                        timer.mark("backend_play_start", node=node.identifier)
                        timer.mark("prepared_track_used", node=node.identifier)
                    log_event(
                        "lavalink-node play using prepared track "
                        f"guild={guild_id} node={node.label} title={track.youtube_search.title}"
                    )
                    await player.play(track=prepared)
                except Exception as exc:
                    if self._end_callbacks.get(guild_id) is on_end:
                        self._end_callbacks.pop(guild_id, None)
                        self._track_errors.pop(guild_id, None)
                    fallback_reason = "prepared_track_failed"
                    log_exception(
                        "lavalink-node play fallback direct load "
                        f"guild={guild_id} node={node.label} "
                        f"track_id={track.youtube_search.video_id or 'unknown'} "
                        f"reason={fallback_reason}",
                        exc,
                    )
            else:
                fallback_reason = (
                    "missing_prepared_track" if prepared is None else "prepared_track_node_mismatch"
                )
                if timer:
                    timer.mark("backend_play_start", node=node.identifier)
                log_event(
                    "lavalink-node play fallback direct load "
                    f"guild={guild_id} node={node.label} reason={fallback_reason}"
                )

            if fallback_reason is not None:
                if timer:
                    timer.mark("direct_load_fallback_start", reason=fallback_reason)
                try:
                    results = await player.get_tracks(query=track.youtube_search.video_url)
                except Exception as exc:
                    if self._end_callbacks.get(guild_id) is on_end:
                        self._end_callbacks.pop(guild_id, None)
                        self._track_errors.pop(guild_id, None)
                    log_exception(
                        "lavalink-node direct load failed "
                        f"guild_id={guild_id} node={node.label} "
                        f"track_id={track.youtube_search.video_id or 'unknown'} "
                        f"reason={fallback_reason}",
                        exc,
                    )
                    raise
                tracks = self._result_tracks(results)
                if not tracks:
                    raise RuntimeError("노드가 트랙을 찾지 못함")
                if timer:
                    timer.mark("direct_load_fallback_done", reason=fallback_reason)
                self._end_callbacks[guild_id] = on_end
                self._track_errors.pop(guild_id, None)
                try:
                    await player.play(track=tracks[0])
                except Exception as exc:
                    if self._end_callbacks.get(guild_id) is on_end:
                        self._end_callbacks.pop(guild_id, None)
                        self._track_errors.pop(guild_id, None)
                    log_exception(
                        "lavalink-node player play failed "
                        f"guild_id={guild_id} node={node.label} "
                        f"track_id={track.youtube_search.video_id or 'unknown'} "
                        f"mode=direct_load",
                        exc,
                    )
                    raise

            if timer:
                timer.mark("backend_play_done", node=node.identifier)
            log_event(
                f"lavalink-node play guild={guild_id} node={node.label} title={track.youtube_search.title}"
            )
            return node

        await self._pool.run_with_failover(action)

    # 제어 동작은 best-effort: 노드 오류(예: 404 Session not found, 죽은 노드)로 인해
    # 호출부(특히 on_voice_state_update 정리 핸들러)가 죽지 않도록 예외를 흡수한다.
    async def stop(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        try:
            await player.stop()
        except Exception as exc:
            log_event(f"stop error guild={guild_id}: {exc}")

    async def pause(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        try:
            await player.set_pause(True)
        except Exception as exc:
            log_event(f"pause error guild={guild_id}: {exc}")

    async def resume(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        try:
            await player.set_pause(False)
        except Exception as exc:
            log_event(f"resume error guild={guild_id}: {exc}")

    async def skip(self, guild_id: int) -> None:
        await self.stop(guild_id)

    async def set_volume(self, guild_id: int, volume: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        try:
            await player.set_volume(volume)
        except Exception as exc:
            log_event(f"set_volume error guild={guild_id}: {exc}")

    async def is_playing(self, guild_id: int) -> bool:
        player = self._players.get(guild_id)
        if player is None:
            return False
        try:
            return bool(player.is_playing() if callable(player.is_playing) else player.is_playing)
        except Exception:
            return False

    async def disconnect(self, guild_id: int) -> None:
        self._end_callbacks.pop(guild_id, None)
        self._track_errors.pop(guild_id, None)

        player = self._players.pop(guild_id, None)
        if player is None:
            return
        try:
            await player.destroy()
        except Exception as exc:
            log_event(f"disconnect error guild={guild_id}: {exc}")

    # ------------------------------------------------------------------ #
    # -nodes 명령용
    # ------------------------------------------------------------------ #
    async def refresh_nodes(self):
        """노드 풀 재탐색 + pomice 노드 재등록. 재생 가능 노드(NodeInfo) 목록 반환."""
        async with self._register_lock:
            self._end_callbacks.clear()
            self._track_errors.clear()
            for guild_id, player in list(self._players.items()):
                try:
                    await player.destroy()
                except Exception:
                    pass
            self._players.clear()

            # 우리가 등록한 노드만 개별 해제(pomice NodePool.disconnect 의 KeyError 회피).
            for nid in list(self._created):
                pnode = self._pomice.NodePool._nodes.get(nid)
                if pnode is not None:
                    try:
                        await pnode.disconnect()  # 내부에서 _nodes 에서 자신을 제거
                    except Exception as exc:
                        log_event(f"node disconnect error {nid}: {exc}")
                self._pomice.NodePool._nodes.pop(nid, None)  # 잔여분 정리(안전)
            self._created.clear()

            await self._register_pool_nodes()
            return self._pool.healthy_nodes
