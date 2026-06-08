"""lavalink-node 엔진 로컬 단일 클라 검증 스크립트 (디스코드 불필요).

목적
----
디스코드에 봇을 붙이기 전에, 맥북에서 이 기능의 핵심(공개 노드 판별 + 순서 기반 sticky failover)을
실제로 검증한다. core/audio/lavalink_node_pool.py 의 LavalinkNodePool 을 그대로 사용한다.

흐름
----
1. discover() : lavalink-list 에서 노드를 받아 각 노드의 YouTube 재생 가능 여부 판별 → 통과 노드 목록 출력.
2. 사용자에게 검색어/URL 입력받기.
3. pool.run_with_failover(action) : sticky 노드부터 순서대로 시도.
   - action(node) = 그 노드의 /v4/loadtracks 로 곡을 resolve(노드가 실제로 YouTube 에서 가져오는지 실연)
                    → resolve 된 YouTube URL 을 yt-dlp 로 임시 파일에 다운로드 → ffplay 로 실제 재생.
   - 실패 시 예외 → 풀이 자동으로 다음 노드로 failover.
4. 2회차 재생을 한 번 더 돌려 sticky 포인터가 "직전 성공 노드"부터 시작하는지 확인.

중요 (로컬 재생에 관한 주석)
---------------------------
Lavalink 는 본래 오디오를 서버에서 디코딩해 Opus 로 *디스코드 음성 게이트웨이*에 송출한다.
따라서 디스코드 음성 연결 없이는 맥 스피커로 소리를 들을 수 없다.
아래 yt-dlp 다운로드 + ffplay 재생은 그 디스코드 송출을 대신하는 *로컬 테스트 대체물*일 뿐이며,
"이 노드가 해당 곡을 YouTube 에서 정상 resolve 했다 == 재생 가능"을 귀로 확인하기 위한 것이다.
봇 통합(Phase 3)에서는 Lavalink v4 클라이언트가 노드 → 디스코드로 직접 송출한다.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import urllib.parse

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 import 경로에 추가 (python test/lavalink_node_test.py 로 실행 가능하도록)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.audio.lavalink_node_pool import LavalinkNodePool, NodeInfo  # noqa: E402
from core.config import LAVALINK_NODE_PROBE_TIMEOUT  # noqa: E402


async def resolve_on_node(node: NodeInfo, query: str) -> dict:
    """해당 노드의 /v4/loadtracks 로 query 를 resolve 하고 첫 트랙 info 를 반환.

    노드가 곡을 못 가져오면(빈 결과/에러/HTTP 비200) 예외를 던져 failover 를 유발한다.
    """
    identifier = query if query.startswith("http") else f"ytsearch:{query}"
    encoded = urllib.parse.quote(identifier, safe="")
    url = f"{node.rest_base}/v4/loadtracks?identifier={encoded}"
    headers = {"Authorization": node.password}
    timeout = aiohttp.ClientTimeout(total=LAVALINK_NODE_PROBE_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"loadtracks HTTP {resp.status}")
            data = await resp.json(content_type=None)

    load_type = (data or {}).get("loadType")
    payload = (data or {}).get("data")
    if load_type == "search":
        tracks = payload or []
    elif load_type == "track":
        tracks = [payload] if payload else []
    else:
        tracks = []
    if not tracks:
        raise RuntimeError(f"no tracks (loadType={load_type})")

    return tracks[0].get("info", {})


def play_locally(youtube_url: str) -> None:
    """yt-dlp 로 오디오를 임시 파일에 받고 ffplay 로 재생 (디스코드 송출의 로컬 대체물)."""
    import yt_dlp

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "track.%(ext)s")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        cookies = os.path.join(os.getcwd(), "cookies.txt")
        if os.path.exists(cookies):
            ydl_opts["cookiefile"] = cookies

        print(f"  [yt-dlp] downloading {youtube_url} ...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])

        files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
        if not files:
            raise RuntimeError("yt-dlp produced no file")
        audio_path = files[0]

        print(f"  [ffplay] playing {os.path.basename(audio_path)} (Ctrl+C 로 중단) ...")
        # -nodisp: 창 없이, -autoexit: 끝나면 종료. 실제로 맥 스피커로 소리가 난다.
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", audio_path],
            check=True,
        )


def make_action(query: str):
    async def action(node: NodeInfo):
        print(f"\n>>> trying node {node.label} ...")
        info = await resolve_on_node(node, query)
        title = info.get("title")
        uri = info.get("uri")
        print(f"  resolved: {title}")
        print(f"  uri: {uri}")
        if not uri:
            raise RuntimeError("resolved track has no uri")
        # 실제 재생(로컬 대체물). 다운로드/재생 실패도 예외 → 다음 노드로 failover.
        await asyncio.to_thread(play_locally, uri)
        return {"node": node.label, "title": title, "uri": uri}

    return action


async def main() -> None:
    pool = LavalinkNodePool()

    print("=== 1) 노드 탐색 + YouTube 재생 가능 판별 ===")
    healthy = await pool.discover()
    print(f"\n전체 {len(pool.all_nodes)}개 중 재생 가능 노드 {len(healthy)}개 (순서대로):")
    for i, n in enumerate(healthy):
        print(f"  [{i}] {n.label}  (secure={n.secure})")
    if not healthy:
        print("재생 가능한 노드가 없습니다. 종료.")
        return

    query = input("\n검색어 또는 YouTube URL: ").strip()
    if not query:
        print("빈 입력. 종료.")
        return

    print("\n=== 2) 1회차 재생 (sticky 0번부터 시작, 실패 시 failover) ===")
    result = await pool.run_with_failover(make_action(query))
    print(f"\n1회차 성공: {result['node']} / {result['title']}")
    print(f"현재 sticky 노드: {pool.current_node.label if pool.current_node else None}")

    again = input("\n2회차 재생으로 sticky 동작 확인? (y/N): ").strip().lower()
    if again == "y":
        print("\n=== 3) 2회차 재생 (직전 성공 노드부터 시작해야 함) ===")
        result2 = await pool.run_with_failover(make_action(query))
        print(f"\n2회차 성공: {result2['node']} / {result2['title']}")
        print(f"현재 sticky 노드: {pool.current_node.label if pool.current_node else None}")


if __name__ == "__main__":
    asyncio.run(main())
