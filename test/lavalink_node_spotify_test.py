"""lavalink-node Spotify(LavaSrc) 지원 검증 스크립트 (디스코드/pomice 불필요).

목적
----
봇 코드를 수정하기 전에, "공개 노드(secure v4) 중 실제로 Spotify URL 을 LavaSrc 로 해석하는
노드가 얼마나 되는지"를 순수 REST 로 실측한다. 이걸로 LAVALINK_NODE_SOURCE 의 spotify/both
모드가 실제 공개 노드에서 의미 있게 동작할지(= healthy 풀이 비지 않는지)를 미리 판정한다.

핵심 근거
--------
pomice 에 Spotify 자격증명을 넘기지 않으면 Spotify URL 은 가로채지지 않고 그대로 노드의
loadtracks 로 전달된다. 즉 Spotify 재생은 "노드에 설치된 LavaSrc 플러그인"이 서버사이드에서
해석한다(클라이언트 자격증명 불필요). LavaSrc 가 없는 노드는 빈/에러 응답을 주므로 probe 로
걸러야 한다. 이 스크립트의 spotify probe 는 그 판별을 봇 통합 전에 미리 돌려보는 것이다.

흐름
----
1. fetch_nodes() : lavalink-list 에서 노드 목록(secure v4)을 받는다. (LavalinkNodePool 읽기 전용 재사용)
2. 각 노드 동시에:
   - YouTube probe : 기존 pool.probe_youtube() 재사용.
   - Spotify probe : 노드 loadtracks 에 Spotify 트랙 URL 을 던져 loadType 판정(LavaSrc 여부).
3. 노드별 YES/NO + loadType 표 출력 + 요약(YouTube N / Spotify N / 둘 다 N).
4. (선택) Spotify URL 을 입력하면 spotify-capable 노드에서 실제 resolve 해 반환 트랙 정보를 출력.

실행
----
    python -m test.lavalink_node_spotify_test
    (또는 python test/lavalink_node_spotify_test.py)

환경변수
--------
- LAVALINK_NODE_SPOTIFY_PROBE_URL : probe 용 Spotify 트랙 URL (미설정 시 아래 기본값).
- 그 외 LAVALINK_* 는 core/config.py 와 동일하게 적용(secure-only, 타임아웃, 노드 목록 URL 등).
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.parse

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 import 경로에 추가 (python test/..._test.py 로 직접 실행 가능하도록)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.audio.lavalink_node_pool import LavalinkNodePool, NodeInfo  # noqa: E402
from core.config import LAVALINK_NODE_PROBE_TIMEOUT  # noqa: E402

# LavaSrc 가 Spotify URL 을 받았을 때 "재생 가능한 결과"로 보는 loadType.
#  - track    : 단일 트랙 URL (대부분의 경우)
#  - playlist : 앨범/플레이리스트 URL
#  - search   : 일부 플러그인 구성
_SPOTIFY_PLAYABLE_LOAD_TYPES = {"track", "playlist", "search"}

# 기본 probe URL: 안정적으로 존재하는 공개 트랙(Rick Astley - Never Gonna Give You Up).
_DEFAULT_SPOTIFY_PROBE_URL = "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8"


def spotify_probe_url() -> str:
    return os.getenv("LAVALINK_NODE_SPOTIFY_PROBE_URL", _DEFAULT_SPOTIFY_PROBE_URL).strip()


async def probe_spotify(node: NodeInfo, query: str, *, timeout: float) -> tuple[bool, str]:
    """노드가 Spotify URL 을 LavaSrc 로 해석하는지 /v4/loadtracks 로 확인.

    반환: (지원여부, loadType 또는 오류문자열). probe_youtube 와 동일한 판정 방식.
    """
    encoded = urllib.parse.quote(query, safe="")
    url = f"{node.rest_base}/v4/loadtracks?identifier={encoded}"
    headers = {"Authorization": node.password}
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return False, f"HTTP {resp.status}"
                data = await resp.json(content_type=None)
    except Exception as exc:
        return False, f"error: {exc}"

    load_type = (data or {}).get("loadType")
    payload = (data or {}).get("data")
    has_result = bool(payload) if not isinstance(payload, list) else len(payload) > 0
    ok = load_type in _SPOTIFY_PLAYABLE_LOAD_TYPES and has_result
    return ok, str(load_type)


async def resolve_spotify_on_node(node: NodeInfo, query: str, *, timeout: float) -> list[dict]:
    """spotify-capable 노드에서 Spotify URL 을 실제 resolve 해 트랙 info 리스트를 반환.

    LavaSrc 가 재생 가능한 트랙(보통 YouTube 등으로 미러링된)으로 변환하는지 눈으로 확인하기 위함.
    """
    encoded = urllib.parse.quote(query, safe="")
    url = f"{node.rest_base}/v4/loadtracks?identifier={encoded}"
    headers = {"Authorization": node.password}
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"loadtracks HTTP {resp.status}")
            data = await resp.json(content_type=None)

    load_type = (data or {}).get("loadType")
    payload = (data or {}).get("data")
    if load_type == "track":
        raw = [payload]
    elif load_type == "playlist":
        raw = (payload or {}).get("tracks", [])
    elif load_type == "search":
        raw = payload or []
    else:
        raise RuntimeError(f"재생 불가 loadType={load_type}")

    tracks = []
    for item in raw:
        info = (item or {}).get("info", {})
        tracks.append(
            {
                "title": info.get("title"),
                "author": info.get("author"),
                "length_ms": info.get("length"),
                "uri": info.get("uri"),
                "sourceName": info.get("sourceName"),
            }
        )
    return tracks


async def main() -> None:
    timeout = LAVALINK_NODE_PROBE_TIMEOUT
    probe_url = spotify_probe_url()

    pool = LavalinkNodePool()

    print("=== 1) 노드 목록 fetch (secure-only / v4) ===")
    nodes = await pool.fetch_nodes()
    if not nodes:
        print("노드 목록을 받지 못했습니다. (LAVALINK_NODE_LIST_URL / 네트워크 확인) 종료.")
        return
    print(f"총 {len(nodes)}개 노드 수신\n")
    print(f"Spotify probe URL: {probe_url}\n")

    print("=== 2) 노드별 YouTube / Spotify(LavaSrc) probe (동시 실행) ===")

    async def check(node: NodeInfo) -> dict:
        yt_ok, sp = await asyncio.gather(
            pool.probe_youtube(node, timeout=timeout),
            probe_spotify(node, probe_url, timeout=timeout),
        )
        sp_ok, sp_type = sp
        return {"node": node, "yt": bool(yt_ok), "sp": sp_ok, "sp_type": sp_type}

    results = await asyncio.gather(*(check(n) for n in nodes), return_exceptions=True)
    results = [r for r in results if isinstance(r, dict)]

    print(f"\n{'NODE':<34} {'YOUTUBE':<8} {'SPOTIFY':<8} SPOTIFY_loadType")
    print("-" * 72)
    for r in results:
        print(
            f"{r['node'].label:<34} "
            f"{('YES' if r['yt'] else 'NO'):<8} "
            f"{('YES' if r['sp'] else 'NO'):<8} "
            f"{r['sp_type']}"
        )

    yt_nodes = [r for r in results if r["yt"]]
    sp_nodes = [r for r in results if r["sp"]]
    both_nodes = [r for r in results if r["yt"] and r["sp"]]

    print("\n=== 요약 (LAVALINK_NODE_SOURCE 모드별 healthy 풀 크기 예측) ===")
    print(f"  전체 노드            : {len(results)}")
    print(f"  youtube 모드 (YouTube): {len(yt_nodes)}")
    print(f"  spotify 모드 (Spotify): {len(sp_nodes)}")
    print(f"  both   모드 (둘 다)   : {len(both_nodes)}")
    if not sp_nodes:
        print("\n⚠ Spotify(LavaSrc) 지원 노드가 0개입니다. 현재 공개 노드 목록에서는 spotify/both 모드가 동작하지 않습니다.")

    # 3) (선택) 실제 resolve 확인
    try:
        query = input("\n[선택] 실제 resolve 해볼 Spotify URL (엔터=건너뜀): ").strip()
    except EOFError:
        query = ""
    if not query:
        print("건너뜀. 종료.")
        return
    if not sp_nodes:
        print("Spotify 지원 노드가 없어 resolve 를 시도할 수 없습니다. 종료.")
        return

    print(f"\n=== 3) Spotify URL resolve (spotify-capable 노드 {len(sp_nodes)}개에 순서대로 시도) ===")
    for r in sp_nodes:
        node = r["node"]
        try:
            tracks = await resolve_spotify_on_node(node, query, timeout=timeout)
        except Exception as exc:
            print(f"  [{node.label}] 실패: {exc} -> 다음 노드")
            continue
        print(f"  [{node.label}] OK, 트랙 {len(tracks)}개:")
        for t in tracks[:5]:
            secs = (t["length_ms"] or 0) / 1000
            print(f"    - {t['title']} / {t['author']} ({secs:.0f}s) source={t['sourceName']}")
            print(f"      uri={t['uri']}")
        if len(tracks) > 5:
            print(f"    ... 외 {len(tracks) - 5}개")
        print("\nresolve 성공. 종료.")
        return
    print("모든 spotify-capable 노드에서 resolve 실패. 종료.")


if __name__ == "__main__":
    asyncio.run(main())
