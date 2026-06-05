"""공개 Lavalink 노드 풀 + 순서 기반 sticky failover.

이 모듈은 DarrenOfficial/lavalink-list 의 공개 노드 목록을 받아와서, 각 노드가 실제로
YouTube 재생이 가능한지 판별하고(= /v4/loadtracks 호출), 통과한 노드들을 순서대로 묶어
failover 재생을 지원한다.

설계 원칙
---------
* 순수 async 로직만 둔다. discord.py / pomice / wavelink 등 어떤 외부 lavalink 라이브러리도
  import 하지 않는다. 덕분에 디스코드 연결 없이 맥북에서 단일 스크립트(test/lavalink_node_test.py)로
  그대로 재사용해 검증할 수 있고, 봇 통합(LavalinkNodeBackend)도 이 풀을 공유한다.
* "노드별 재생 동작"은 호출자가 콜백(action)으로 주입한다. 로컬 테스트는 yt-dlp+ffplay로,
  봇은 Lavalink v4 클라이언트로 같은 failover 로직을 태운다.

failover 규칙
-------------
* 노드 목록은 순서가 있다. 재생은 항상 "현재 sticky 포인터"가 가리키는 노드부터 시작한다.
* 1번 실패 → 2번 재시도 → 2번 실패 → 3번 ... (순서대로 회전).
* 3번에서 성공하면 sticky 포인터를 3번으로 옮긴다. 즉 다음 재생 시도는 무조건 3번부터 시작한다.
* 실패한 노드는 **이번 시도에서만 건너뛰고 풀에는 그대로 남긴다**(영구 제거 X). 공개 노드는 간헐적
  502 등을 내므로, 영구 제거하면 풀이 금세 비어 음악이 멈추기 때문. 다음 재생에서 다시 시도된다.
* 죽은 노드의 실제 제거/목록 갱신은 discover()(봇 재시작 / -nodes 명령)가 담당하며, 이때 sticky 도 리셋된다.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, TypeVar

import aiohttp

from core.config import (
    LAVALINK_LIST_URL,
    LAVALINK_NODE_MAX_FAILOVER,
    LAVALINK_NODE_PROBE_QUERY,
    LAVALINK_NODE_PROBE_TIMEOUT,
    LAVALINK_NODE_SECURE_ONLY,
    LAVALINK_NODE_SOURCE,
    LAVALINK_NODE_SPOTIFY_PROBE_URL,
)
from core.util import log_event

T = TypeVar("T")

# loadtracks 가 "재생 가능한 결과"로 간주하는 loadType 들 (Lavalink v4).
_PLAYABLE_LOAD_TYPES = {"search", "track"}
# Spotify URL 을 LavaSrc 가 해석했을 때 나오는 loadType 들 (단일=track, 앨범/플레이리스트=playlist).
_SPOTIFY_PLAYABLE_LOAD_TYPES = {"track", "playlist", "search"}


@dataclass
class NodeInfo:
    """lavalink-list API 한 항목. 필드명은 API 응답(JSON) 기준."""

    unique_id: str
    identifier: str
    host: str
    port: int
    password: str
    secure: bool
    version: str

    @property
    def scheme(self) -> str:
        return "https" if self.secure else "http"

    @property
    def ws_scheme(self) -> str:
        return "wss" if self.secure else "ws"

    @property
    def rest_base(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def from_api(cls, raw: dict) -> "NodeInfo":
        return cls(
            unique_id=str(raw.get("unique-id") or raw.get("identifier") or ""),
            identifier=str(raw.get("identifier") or raw.get("unique-id") or ""),
            host=str(raw["host"]),
            port=int(raw["port"]),
            password=str(raw.get("password") or ""),
            secure=bool(raw.get("secure", False)),
            version=str(raw.get("version") or ""),
        )


class LavalinkNodePool:
    def __init__(self, *, secure_only: Optional[bool] = None) -> None:
        self._all: List[NodeInfo] = []      # API 원본 순서 유지
        self._healthy: List[NodeInfo] = []  # YouTube 판별 통과 노드, 순서 유지
        # sticky 포인터: 다음 재생을 "시작"할 _healthy 내 인덱스.
        self._current_index: int = 0
        # secure(wss) 노드만 쓸지. None 이면 config(LAVALINK_NODE_SECURE_ONLY) 기본값 사용.
        # 음성 자격증명 평문 노출 방지용 (config 주석 참고).
        self._secure_only: bool = (
            LAVALINK_NODE_SECURE_ONLY if secure_only is None else secure_only
        )

    # ------------------------------------------------------------------ #
    # 조회
    # ------------------------------------------------------------------ #
    @property
    def healthy_nodes(self) -> List[NodeInfo]:
        return list(self._healthy)

    @property
    def all_nodes(self) -> List[NodeInfo]:
        return list(self._all)

    @property
    def current_node(self) -> Optional[NodeInfo]:
        if 0 <= self._current_index < len(self._healthy):
            return self._healthy[self._current_index]
        return None

    # ------------------------------------------------------------------ #
    # 1) 노드 목록 fetch
    # ------------------------------------------------------------------ #
    async def fetch_nodes(self, *, timeout: Optional[float] = None) -> List[NodeInfo]:
        """lavalink-list REST API 에서 노드 목록을 받아 v4 노드만 반환한다.

        실패/타임아웃 시 빈 목록을 반환하고 로그를 남긴다(예외를 던지지 않음).
        """
        timeout = aiohttp.ClientTimeout(total=timeout or LAVALINK_NODE_PROBE_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(LAVALINK_LIST_URL) as resp:
                    if resp.status != 200:
                        log_event(f"fetch_nodes HTTP {resp.status} from {LAVALINK_LIST_URL}")
                        self._all = []
                        return []
                    data = await resp.json(content_type=None)
        except Exception as exc:  # 네트워크/파싱 오류 전부 흡수
            log_event(f"fetch_nodes failed: {exc}")
            self._all = []
            return []

        nodes: List[NodeInfo] = []
        for raw in data or []:
            try:
                node = NodeInfo.from_api(raw)
            except Exception as exc:
                log_event(f"fetch_nodes skip malformed entry: {exc}")
                continue
            # pomice 미사용이지만 우리 v4 클라이언트도 v4 전용이므로 v4만 사용.
            if node.version.lower() != "v4":
                continue
            # secure-only 모드면 non-secure 노드 제외 (음성 토큰 평문 노출 방지).
            if self._secure_only and not node.secure:
                continue
            nodes.append(node)

        self._all = nodes
        secure_note = " (secure-only)" if self._secure_only else ""
        log_event(f"fetch_nodes ok: {len(nodes)} v4 nodes from list{secure_note}")
        return nodes

    # ------------------------------------------------------------------ #
    # 2) YouTube 재생 가능 여부 판별 (= loadtracks)
    # ------------------------------------------------------------------ #
    async def probe_youtube(self, node: NodeInfo, *, timeout: Optional[float] = None) -> bool:
        """노드가 YouTube 검색/로드를 실제로 처리하는지 /v4/loadtracks 로 확인.

        많은 공개 노드가 YouTube 를 막아두거나 죽어 있으므로(연결 거부/500 등) 이 판별이
        이 기능의 핵심이다. 디스코드/음성 연결과 무관하게 순수 REST 로 확인한다.
        """
        identifier = f"ytsearch:{LAVALINK_NODE_PROBE_QUERY}"
        encoded = urllib.parse.quote(identifier, safe="")
        url = f"{node.rest_base}/v4/loadtracks?identifier={encoded}"
        headers = {"Authorization": node.password}
        timeout = aiohttp.ClientTimeout(total=timeout or LAVALINK_NODE_PROBE_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        log_event(f"probe {node.label} HTTP {resp.status} -> NO")
                        return False
                    data = await resp.json(content_type=None)
        except Exception as exc:
            log_event(f"probe {node.label} error: {exc} -> NO")
            return False

        load_type = (data or {}).get("loadType")
        payload = (data or {}).get("data")
        # v4: search -> data 는 트랙 리스트, track -> data 는 단일 트랙 객체.
        has_result = bool(payload) if not isinstance(payload, list) else len(payload) > 0
        ok = load_type in _PLAYABLE_LOAD_TYPES and has_result
        log_event(f"probe {node.label} loadType={load_type} -> {'YES' if ok else 'NO'}")
        return ok

    async def probe_spotify(self, node: NodeInfo, *, timeout: Optional[float] = None) -> bool:
        """노드가 Spotify URL 을 LavaSrc 플러그인으로 해석하는지 /v4/loadtracks 로 확인.

        Spotify 재생은 클라이언트(pomice) 가 아니라 노드의 LavaSrc 가 서버사이드에서 처리한다.
        LavaSrc 가 없는 노드는 Spotify URL 에 빈/에러 응답을 주므로(probe_youtube 와 동일 판정)
        이 판별을 통과한 노드만 spotify/both 모드에서 사용한다.
        """
        encoded = urllib.parse.quote(LAVALINK_NODE_SPOTIFY_PROBE_URL, safe="")
        url = f"{node.rest_base}/v4/loadtracks?identifier={encoded}"
        headers = {"Authorization": node.password}
        timeout = aiohttp.ClientTimeout(total=timeout or LAVALINK_NODE_PROBE_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        log_event(f"probe(spotify) {node.label} HTTP {resp.status} -> NO")
                        return False
                    data = await resp.json(content_type=None)
        except Exception as exc:
            log_event(f"probe(spotify) {node.label} error: {exc} -> NO")
            return False

        load_type = (data or {}).get("loadType")
        payload = (data or {}).get("data")
        has_result = bool(payload) if not isinstance(payload, list) else len(payload) > 0
        ok = load_type in _SPOTIFY_PLAYABLE_LOAD_TYPES and has_result
        log_event(f"probe(spotify) {node.label} loadType={load_type} -> {'YES' if ok else 'NO'}")
        return ok

    async def probe_source(self, node: NodeInfo, *, timeout: Optional[float] = None) -> bool:
        """LAVALINK_NODE_SOURCE 모드에 맞춰 노드의 재생 가능 여부를 판별한다.

        - youtube : YouTube probe
        - spotify : Spotify(LavaSrc) probe
        - both    : 둘 다 통과해야 함(동시 실행 → 같은 timeout 예산 공유, 합산 아님)
        """
        if LAVALINK_NODE_SOURCE == "spotify":
            return await self.probe_spotify(node, timeout=timeout)
        if LAVALINK_NODE_SOURCE == "both":
            yt_ok, sp_ok = await asyncio.gather(
                self.probe_youtube(node, timeout=timeout),
                self.probe_spotify(node, timeout=timeout),
            )
            return bool(yt_ok) and bool(sp_ok)
        return await self.probe_youtube(node, timeout=timeout)

    # ------------------------------------------------------------------ #
    # 3) 재탐색 (시작 시 자동 / -nodes 명령 / 재시작)
    # ------------------------------------------------------------------ #
    async def discover(self) -> List[NodeInfo]:
        """노드 목록을 받아 전부 동시에 판별하고, 통과 노드로 풀을 리빌드한다.

        discover() 가 불릴 때마다 풀과 sticky 포인터가 완전히 리셋된다(실패처리 이력 포함).
        """
        await self.fetch_nodes()
        if not self._all:
            self._healthy = []
            self._current_index = 0
            return []

        results = await asyncio.gather(
            *(self.probe_source(node) for node in self._all),
            return_exceptions=True,
        )
        healthy = [node for node, ok in zip(self._all, results) if ok is True]

        self._healthy = healthy
        self._current_index = 0  # 재탐색 = sticky 리셋
        log_event(f"discover[{LAVALINK_NODE_SOURCE}]: {len(healthy)}/{len(self._all)} playable nodes")
        return list(healthy)

    def reset(self) -> None:
        """sticky 포인터만 처음으로 되돌린다(노드 목록은 유지)."""
        self._current_index = 0

    def set_healthy(self, nodes: List[NodeInfo]) -> None:
        """failover 대상 노드를 명시적으로 지정(실제 연결에 성공한 노드만 남길 때 사용)."""
        self._healthy = list(nodes)
        self._current_index = 0

    # ------------------------------------------------------------------ #
    # 4) sticky failover 코어
    # ------------------------------------------------------------------ #
    async def run_with_failover(self, action: Callable[[NodeInfo], Awaitable[T]]) -> T:
        """현재 sticky 노드부터 순서대로(회전) action 을 시도한다.

        action(node) 는 그 노드로 재생을 시도하고, 실패하면 예외를 던져야 한다.
        - 성공: sticky 포인터를 그 노드로 옮기고(다음 시도도 여기서 시작) 결과를 반환.
        - 실패: 다음 노드로 넘어간다. **노드를 풀에서 제거하지 않는다**(이번 호출에서만 건너뜀).
        - 이번 호출에서 모든 노드 실패: RuntimeError (단, 풀은 그대로 유지).

        예) 노드 [A, B, C], sticky=A 에서 A 실패→B 실패→C 성공 이면 sticky=C 가 되어 다음 재생은 C 부터 시작.

        주의(설계 변경): 공개 노드는 간헐적으로 502 등을 내므로, 실패했다고 영구 제거하면 풀이 금세
        비어 음악이 멈춘다. 그래서 실패 노드를 영구 제거하지 않고 **이번 시도에서만 건너뛰고 다음 재생에서
        다시 시도**한다. 노드 목록 자체의 갱신(죽은 노드 제거)은 discover()(재시작 / -nodes)가 담당한다.
        동시 호출(검색+재생 등)에도 안전하도록 노드 리스트 스냅샷으로 순회한다.

        과도한 재시도 방지: 한 호출에서 최대 LAVALINK_NODE_MAX_FAILOVER(기본 3)개 노드까지만 시도하고,
        그 안에 성공 못 하면 이번 요청은 포기(RuntimeError)한다. 다음(새) 요청은 다시 새 예산으로 시작.
        """
        nodes = list(self._healthy)  # 스냅샷: 동시 호출/리스트 변경에 안전
        n = len(nodes)
        if n == 0:
            raise RuntimeError("사용 가능한 Lavalink 노드가 없습니다. (discover 필요)")

        limit = n if LAVALINK_NODE_MAX_FAILOVER <= 0 else min(LAVALINK_NODE_MAX_FAILOVER, n)
        start = self._current_index % n
        last_error: Optional[Exception] = None
        for offset in range(limit):
            node = nodes[(start + offset) % n]
            try:
                result = await action(node)
            except Exception as exc:
                last_error = exc
                log_event(f"failover: node {node.label} failed, trying next ({exc})")
                continue

            # 성공 → sticky 포인터를 이 노드로(현재 _healthy 기준 인덱스로 환산).
            try:
                self._current_index = self._healthy.index(node)
            except ValueError:
                self._current_index = 0
            log_event(f"failover: node {node.label} OK -> sticky set")
            return result

        raise RuntimeError(
            f"failover 실패 ({limit}/{n}개 노드 시도, 최대 {LAVALINK_NODE_MAX_FAILOVER}). 마지막 오류: {last_error}"
        )
