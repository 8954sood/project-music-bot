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
from typing import Dict, Optional, Set

import discord

from core.audio.backend import AudioBackend, OnTrackEnd
from core.audio.lavalink_node_pool import LavalinkNodePool, NodeInfo
from core.config import LAVALINK_NODE_PROBE_TIMEOUT
from core.model.music_application import MusicApplication
from core.util import log_event


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
        self._monitor_tasks: Dict[int, asyncio.Task] = {}
        self._created: Set[str] = set()  # create_node 성공한 노드 identifier
        self._register_lock = asyncio.Lock()  # discover/등록 동시 실행 직렬화

    # ------------------------------------------------------------------ #
    # 수명주기
    # ------------------------------------------------------------------ #
    async def connect(self, bot: discord.Client) -> None:
        self._bot = bot
        async with self._register_lock:
            await self._register_pool_nodes()

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
        log_event(f"lavalink-node: {len(self._created)}/{len(nodes)} pomice nodes connected")

    async def _check_and_register(self, node: NodeInfo, deadline: float) -> Optional[str]:
        """노드 1개: YouTube 판별 → /version 연결 → ready 대기 → 버전보정. 성공 시 identifier.

        모든 네트워크 단계가 공유 deadline 까지 남은 시간으로 제한된다(probe+register 총합이
        LAVALINK_NODE_PROBE_TIMEOUT 을 넘지 않음).
        """
        from pomice.utils import LavalinkVersion

        loop = asyncio.get_event_loop()
        remaining = lambda: max(0.1, deadline - loop.time())  # noqa: E731

        # 1) YouTube 재생 가능 판별
        try:
            ok = await self._pool.probe_youtube(node, timeout=remaining())
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
            log_event(f"create_node failed (skipped) node={node.label}: {exc}")
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
                self._players[guild_id] = player
                return
            # 다른 채널이거나 stale 상태 → 깔끔히 끊고 새로 연결("Already connected" 방지).
            try:
                await player.disconnect(force=True)
            except Exception:
                pass
            self._players.pop(guild_id, None)

        player = await voice_channel.connect(cls=self._pomice.Player)
        self._players[guild_id] = player

    async def get_tracks(self, query: str):
        """검색용 loadtracks. sticky 노드부터 failover 로 시도하고 pomice 원시 결과를 반환한다.

        - 결과 없음(loadType empty)은 노드 실패가 아니라 정상 응답이므로 그대로(None/빈 결과) 반환한다.
          (그렇지 않으면 매칭 없는 검색어 하나가 멀쩡한 노드를 전부 제거해버린다.)
        - 노드가 에러를 던지면(get_tracks 예외) failover 로 다음 노드를 시도한다.
        - 사용 가능한 노드가 없거나 전부 실패하면 RuntimeError (호출부에서 빈 결과로 처리).
        """
        async def action(node: NodeInfo):
            target = self._pomice.NodePool.get_node(identifier=node.identifier)
            return await target.get_tracks(query=query)

        return await self._pool.run_with_failover(action)

    async def play(self, guild_id: int, track: MusicApplication, on_end: OnTrackEnd) -> None:
        player = self._players.get(guild_id)
        if player is None or not self._is_connected(player):
            raise RuntimeError("Lavalink player is not connected.")

        async def action(node: NodeInfo):
            target = self._pomice.NodePool.get_node(identifier=node.identifier)  # 없으면 예외→failover
            if player.node is None or player.node._identifier != node.identifier:
                await self._swap_to(player, target)

            results = await player.get_tracks(query=track.youtube_search.video_url)
            if results is None:
                raise RuntimeError("노드가 트랙을 찾지 못함")
            if isinstance(results, list):
                tracks = results
            elif hasattr(results, "tracks"):
                tracks = list(results.tracks)
            else:
                tracks = []
            if not tracks:
                raise RuntimeError("노드가 트랙을 찾지 못함")

            await player.play(track=tracks[0])
            self._start_monitor(guild_id, player, on_end)
            log_event(
                f"lavalink-node play guild={guild_id} node={node.label} title={track.youtube_search.title}"
            )
            return node

        await self._pool.run_with_failover(action)

    def _start_monitor(self, guild_id: int, player, on_end: OnTrackEnd) -> None:
        task = self._monitor_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

        async def _monitor() -> None:
            # 트랙 종료를 폴링으로 감지 (기존 lavalink_backend 와 동일 방식, cog 리스너 불필요).
            while True:
                await asyncio.sleep(1)
                if getattr(player, "is_dead", False):
                    break
                is_playing = player.is_playing() if callable(player.is_playing) else player.is_playing
                is_paused = player.is_paused() if callable(player.is_paused) else player.is_paused
                if not is_playing and not is_paused and player.current is None:
                    break
            on_end(None)

        self._monitor_tasks[guild_id] = asyncio.create_task(_monitor())

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
        task = self._monitor_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

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
            for task in list(self._monitor_tasks.values()):
                task.cancel()
            self._monitor_tasks.clear()
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
