# Phase 3 핸드오프: `lavalink-node` 봇 디스코드 통합 (pomice 미사용, v4 직접 구현)

> 이 문서는 **다른 세션**이 Phase 3를 이어서 구현하기 위한 인수인계 문서다.
> Phase 1·2(공개 노드 풀 + sticky failover + 로컬 단일 클라 검증)는 **이미 완료·검증**되었다.

---

## 0. 현재까지 완료된 것 (Phase 1·2)

| 파일 | 상태 | 내용 |
|------|------|------|
| `core/config.py` | 수정됨 | `LAVALINK_LIST_URL`, `LAVALINK_NODE_PROBE_TIMEOUT`, `LAVALINK_NODE_PROBE_QUERY`, `LAVALINK_NODE_SECURE_ONLY` 추가 |
| `core/audio/lavalink_node_pool.py` | **신규·검증됨** | 노드 fetch + YouTube 판별 + 순서기반 sticky failover. discord/pomice 무의존 |
| `core/audio/__init__.py` | 수정됨 | PEP 562 lazy import (순수 모듈을 discord 없이 import 가능하게) |
| `test/lavalink_node_test.py` | 신규·검증됨 | 로컬 단일 클라 검증: discover → loadtracks resolve → yt-dlp 다운로드 → ffplay 실제 재생 |
| `.gitignore` | 수정됨 | `.venv-test` 추가 |

**검증 결과**: 노드 11개 중 죽은 노드 자동 제외, failover 5종 단위테스트 통과, end-to-end ffplay 실제 재생 exit 0 확인.

⚠️ **`.env.example` 은 보안 훅이 수정을 차단**한다. 아래 env 변수들을 직접 `.env`에 추가해야 한다(섹션 6).

---

## 1. Phase 3 목표

`AUDIO_BACKEND=lavalink-node` 일 때, 공개 노드 풀을 써서 디스코드 음성 채널로 실제 음악을 송출한다.
**pomice/wavelink 등 외부 lavalink 라이브러리를 쓰지 않고 Lavalink v4 프로토콜(REST + WebSocket)을 직접 구현**한다.
(사용자 명시 지시. memory: `lavalink-no-pomice`)

failover/노드선택 로직은 **이미 만든 `LavalinkNodePool`을 그대로 재사용**한다. Phase 3는 "노드별 재생 동작(action)"만 v4 클라이언트로 채우면 된다.

---

## 2. 재사용할 공유 API — `core/audio/lavalink_node_pool.py`

```python
@dataclass
class NodeInfo:
    unique_id: str; identifier: str; host: str; port: int
    password: str; secure: bool; version: str
    # 파생 프로퍼티: scheme(http/https), ws_scheme(ws/wss), rest_base, label

class LavalinkNodePool:
    def __init__(self, *, secure_only: Optional[bool] = None): ...
    async def fetch_nodes(self) -> List[NodeInfo]      # /All 에서 v4(+secure_only) 필터
    async def probe_youtube(self, node) -> bool        # /v4/loadtracks 로 YT 가능 판별
    async def discover(self) -> List[NodeInfo]         # fetch+probe 전체, 풀 리빌드, sticky 리셋
    def reset(self) -> None                            # sticky 포인터만 0으로
    async def run_with_failover(self, action: Callable[[NodeInfo], Awaitable[T]]) -> T
    # 프로퍼티: healthy_nodes, all_nodes, current_node
```

**`run_with_failover(action)` 계약** (Phase 3에서 핵심):
- `action(node)` = 그 노드로 재생 시도. **성공하면 반환값, 실패하면 예외를 던져야 한다.**
- 풀이 알아서: sticky 노드부터 순서대로 시도 → 성공 노드를 sticky로 고정(다음 재생도 거기서 시작) → 실패 노드는 풀에서 제거.
- 전부 실패하면 `RuntimeError`.

---

## 3. 신규: `core/audio/lavalink_v4_client.py` (노드 1개 = 세션 1개)

`aiohttp`로 REST + WebSocket만 사용. pomice 대체. **노드 하나에 대한 연결/세션**을 캡슐화한다.
failover를 위해 노드별로 별도 인스턴스를 둔다(또는 sticky 노드만 지연 연결).

### 3-1. WebSocket
- 연결: `{ws_scheme}://{host}:{port}/v4/websocket`
- 헤더:
  - `Authorization: <node.password>`
  - `User-Id: <bot.user.id>`
  - `Client-Name: project-music-bot/1.0`
- 수신 메시지(JSON) `op` 분기:
  - `ready` → `sessionId` 저장 (이후 REST player 경로에 사용). `resumed` 여부 무시 가능.
  - `event` → `type`:
    - `TrackEndEvent` (`reason` in `finished/loadFailed/...`) → 해당 guild 의 `on_end(None)` 호출.
      (기존 LavalinkBackend 의 폴링 방식보다 정확 — 폴링 불필요)
    - `TrackExceptionEvent` / `TrackStuckEvent` → 재생 실패로 처리(필요시 on_end + 로깅).
  - `playerUpdate` → position 등 상태(선택적으로 사용).
  - `stats` → 노드 부하(선택).
- 끊기면 재연결 로직 필요(간단히: 재생 시도 시 세션 없으면 재연결).

### 3-2. REST (헤더 `Authorization: <password>`, `Content-Type: application/json`)
- 검색/판별: `GET /v4/loadtracks?identifier=<urlencode(ytsearch:.. 또는 url)>`
  - v4 응답: `{ "loadType": "search"|"track"|"empty"|"error", "data": ... }`
  - `search` → `data` 는 트랙 리스트, `track` → `data` 는 단일 트랙. 각 트랙에 `encoded`(base64) + `info`.
  - **재생에는 `encoded` 문자열이 필요**하다 (loadtracks 로 얻어 player PATCH 에 넣음).
- 재생/업데이트: `PATCH /v4/sessions/{sessionId}/players/{guildId}?noReplace=false`
  - body 예: `{ "track": { "encoded": "<base64>" }, "volume": 100, "paused": false, "voice": {...} }`
  - 일시정지: `{ "paused": true }` / 볼륨: `{ "volume": 0~1000 }`
  - 정지(트랙만 제거): `{ "track": { "encoded": null } }`
- 플레이어 제거(연결 해제): `DELETE /v4/sessions/{sessionId}/players/{guildId}`

### 3-3. 음성 브리지 (가장 어려운 부분)
Lavalink는 봇 대신 음성 UDP를 연다. 그러려면 **디스코드가 봇에게 주는 음성 자격증명을 노드에 전달**해야 한다.
- discord.py 에서 **커스텀 `discord.VoiceProtocol` 서브클래스**를 만든다. 이게 핵심.
  - `await voice_channel.connect(cls=LavalinkVoiceProtocol)` 형태로 입장.
  - `on_voice_server_update(data)` → `data["endpoint"]`, `data["token"]` 확보.
  - `on_voice_state_update(data)` → `data["session_id"]` 확보.
  - 셋 다 모이면 player PATCH 의 `voice` 객체로 노드에 전달:
    ```json
    "voice": { "token": "...", "endpoint": "...", "sessionId": "..." }
    ```
  - 자체 UDP 음성은 열지 않는다(노드가 연다).
- 참고 구현: lavalink.py / wavelink / pomice 의 VoiceProtocol 소스가 좋은 레퍼런스(코드 복붙 말고 패턴만).
- **공식 v4 문서로 payload 형식·필드명을 반드시 확정**할 것.

---

## 4. 신규: `core/audio/lavalink_node_backend.py` (`AudioBackend` 구현)

`core/audio/backend.py` 의 `AudioBackend` ABC 를 구현. 시그니처는 기존 `lavalink_backend.py` 와 동일하게:

```python
async def connect(self, bot)                         # pool.discover() 실행. WS는 지연연결.
async def ensure_player(self, guild_id, voice_channel) # 커스텀 VoiceProtocol 로 음성 입장
async def play(self, guild_id, track, on_end)          # ↓ failover 로 송출
async def stop / pause / resume / skip / set_volume / is_playing / disconnect
```

`play()` 핵심 — 풀의 failover 에 v4 action 주입:
```python
async def play(self, guild_id, track, on_end):
    async def action(node: NodeInfo):
        client = self._client_for(node)          # 없으면 v4 WS 연결
        loaded = await client.loadtracks(track.youtube_search.video_url)  # encoded 획득
        if not loaded: raise RuntimeError("no track")  # → 풀이 다음 노드로 failover
        await client.play(guild_id, loaded[0].encoded, voice=self._voice_of(guild_id))
        client.register_on_end(guild_id, on_end)  # TrackEndEvent 시 호출
        return node
    await self._pool.run_with_failover(action)
```
- `MusicApplication.youtube_search.video_url` 로 노드에서 resolve (기존 LavalinkBackend 와 동일 패턴).
- 노드별 세션을 분리해야 failover 시 다른 노드로 깔끔히 전환된다.
- `refresh_nodes()` 메서드 추가: `await pool.discover()` + 기존 WS 세션 정리 (= `-nodes` 명령용).

---

## 5. 배선

### `core/audio/factory.py`
```python
elif AUDIO_BACKEND == "lavalink-node":
    backend = LavalinkNodeBackend()      # host/port 불필요 — 풀이 노드를 가져옴
```
(import 추가. 기존 분기: `lavalink` / `hybrid` / else=ffmpeg.)

### `app.py` — `-nodes` 소유주 명령 (`-reload` 패턴 `app.py:47` 참고)
```python
@bot.command("nodes")
@commands.is_owner()
async def refresh_nodes(ctx):
    cog = bot.get_cog("Music")
    backend = getattr(cog.audio_service, "backend", None) if cog else None
    if not hasattr(backend, "refresh_nodes"):
        return await ctx.send("현재 엔진은 lavalink-node 가 아닙니다.")
    healthy = await backend.refresh_nodes()   # pool.discover() 재실행 → sticky 리셋
    await ctx.send(f"노드 재탐색 완료: 재생 가능 {len(healthy)}개")
```
- cog 의 audio_service 접근 경로는 `cogs/music.py` 구조 확인 후 확정(`self.audio_service = create_audio_service(bot)`).

---

## 6. ENV 변수 (`.env` 에 직접 추가 — `.env.example` 은 훅이 차단)

```
AUDIO_BACKEND=lavalink-node
LAVALINK_LIST_URL=https://lavalink-list.ajieblogs.eu.org/All
LAVALINK_NODE_PROBE_TIMEOUT=8
LAVALINK_NODE_PROBE_QUERY=lofi hip hop
LAVALINK_NODE_SECURE_ONLY=true
```
- **`LAVALINK_NODE_SECURE_ONLY` 보안 주의**: 봇이 음성 토큰을 노드에 넘기는데 non-secure(http/ws) 노드는
  이를 **평문 전송**한다(도청 위험). 실제 음성 송출에서는 반드시 `true` 유지. config 기본값도 `true`.
  (봇 토큰 자체는 노드에 전달되지 않음 — 노출되는 건 그 음성 연결 한정 자격증명.)

---

## 7. 함정 / 주의사항

1. **루트 `test.py` 가 `test` 패키지명을 가린다.** 로컬 테스트는 `python test/lavalink_node_test.py`
   (직접 경로)로 실행. `python -m test.lavalink_node_test` 는 안 됨.
2. **`core/audio/__init__.py` 는 lazy(PEP 562)** 로 바뀌어 있다. 새 백엔드를 factory 에서 import 하는 건
   문제없지만, 패키지 최상위에서 무거운 모듈을 eager import 로 되돌리지 말 것(로컬 테스트가 discord 를 요구하게 됨).
3. **공개 노드는 매우 불안정**하다. probe 에서 통과해도 재생 중 죽을 수 있으니 failover(이미 구현됨)가 필수.
4. v4 `loadtracks` 응답의 `loadType`/`data` 형식은 v3 와 다르다. 위 3-2 형식 기준으로 파싱.
5. 의존성: aiohttp, yt-dlp 는 `requirements.txt` 에 이미 있음. v4 직접 구현이므로 pomice 는 **불필요**(제거 검토 가능, 단 기존 `lavalink`/`hybrid` 엔진이 아직 pomice 사용 중이니 주의).

---

## 8. 검증 (디스코드 연결 필요 — Phase 3는 로컬만으로 완전 검증 불가)

1. `.env` 에 `AUDIO_BACKEND=lavalink-node` + 위 변수 + `BOT_TOKEN` 설정.
2. `python app.py` → 봇 온라인 → 음성 채널에서 재생 명령 → 소리 송출 확인.
3. 재생 중 sticky 노드를 강제로 막거나(방화벽) 죽은 노드로 시작시켜 **failover 로 다음 노드 전환** 확인.
4. 소유주가 `-nodes` 실행 → 풀 재탐색 + 결과 메시지 확인.
5. `LAVALINK_NODE_SECURE_ONLY=true` 에서 secure 노드만 풀에 들어오는지 로그로 확인.

---

## 9. 참고 파일 (읽고 시작)
- `core/audio/backend.py` — 구현할 ABC
- `core/audio/lavalink_backend.py` — 기존 pomice 버전(패턴 참고, 단 pomice 부분은 v4 직접구현으로 대체)
- `core/audio/service.py` — AudioService 가 backend 를 어떻게 호출하는지(`play`/`on_end` 콜백 흐름)
- `cogs/music.py` — audio_service 생성/사용처, `-nodes` 가 접근할 경로
- `lavalink_search_test.py` — v4 loadtracks REST 호출 예시(이미 있음)
- memory: `lavalink-no-pomice`
