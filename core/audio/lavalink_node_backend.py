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

    # ------------------------------------------------------------------ #
    # 수명주기
    # ------------------------------------------------------------------ #
    async def connect(self, bot: discord.Client) -> None:
        self._bot = bot
        await self._register_pool_nodes()

    async def _register_pool_nodes(self) -> None:
        """풀을 탐색(REST 판별)한 뒤 통과 노드를 pomice 노드로 등록한다."""
        healthy = await self._pool.discover()
        self._created.clear()
        for node in healthy:
            try:
                await self._connect_node(node)
                self._created.add(node.identifier)
            except Exception as exc:
                # 공개 노드는 불안정 — 연결 실패는 흡수(풀 failover 가 이후 자동 제외).
                log_event(f"node connect failed node={node.label}: {exc}")
        log_event(f"lavalink-node: {len(self._created)}/{len(healthy)} pomice nodes connected")

    async def _connect_node(self, node: NodeInfo) -> None:
        """pomice 노드를 /version 자동감지 없이 v4 로 고정해 직접 연결한다.

        공개 노드 중에는 /version 엔드포인트가 다른 사이트로 리다이렉트되거나 HTML/502 를 돌려줘서
        pomice 의 기본 create_node(버전 자동감지)가 ContentTypeError 로 실패하는 "특수 구조" 노드가
        있다(예: lavalinkv4.serenetia.com). 우리는 노드 목록 API 에서 이미 version=="v4" 임을 알고
        있으므로, 버전 체크를 건너뛰고(connect(reconnect=True) 는 /version 페치를 하지 않음) v4 로
        고정해 연결한다.

        ⚠️ pomice semi-private 내부에 의존(Node / _version / _available / NodePool._nodes /
           connect(reconnect=True)). pomice 버전 업 시 점검 필요.
        """
        from pomice.utils import LavalinkVersion

        if node.identifier in self._pomice.NodePool._nodes:
            return  # 이미 연결됨

        pnode = self._pomice.Node(
            pool=self._pomice.NodePool,
            bot=self._bot,
            host=node.host,
            port=node.port,
            password=node.password,
            identifier=node.identifier,
            secure=node.secure,
            # fallback=False: 노드 전환(failover)은 우리 풀이 deterministic 하게 담당한다.
            # pomice 자체 node-switch 와 충돌하지 않도록 끈다.
            fallback=False,
        )
        pnode._version = LavalinkVersion(major=4, minor=0, fix=0)
        await pnode.connect(reconnect=True)  # reconnect=True → /version 페치 스킵
        pnode._available = True  # reconnect 경로는 _available 을 직접 세팅하지 않음
        self._pomice.NodePool._nodes[node.identifier] = pnode

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
        player = self._players.get(guild_id)
        if player is not None and self._is_connected(player):
            current_channel = getattr(player, "channel", None)
            if current_channel is not None and current_channel.id == voice_channel.id:
                return
            await player.move_to(voice_channel)
            return

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

    async def stop(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.stop()

    async def pause(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.set_pause(True)

    async def resume(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.set_pause(False)

    async def skip(self, guild_id: int) -> None:
        await self.stop(guild_id)

    async def set_volume(self, guild_id: int, volume: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        await player.set_volume(volume)

    async def is_playing(self, guild_id: int) -> bool:
        player = self._players.get(guild_id)
        if player is None:
            return False
        return bool(player.is_playing() if callable(player.is_playing) else player.is_playing)

    async def disconnect(self, guild_id: int) -> None:
        task = self._monitor_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

        player = self._players.pop(guild_id, None)
        if player is None:
            return
        await player.destroy()

    # ------------------------------------------------------------------ #
    # -nodes 명령용
    # ------------------------------------------------------------------ #
    async def refresh_nodes(self):
        """노드 풀 재탐색 + pomice 노드 재등록. 재생 가능 노드(NodeInfo) 목록 반환."""
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
