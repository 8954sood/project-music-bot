# project-music

[English](README.md) | **한국어**

## 설치
- `.env` 생성(`.env.example` 참고) 후 `BOT_TOKEN` 설정.
- (권장) 가상환경: `python3 -m venv .venv && source .venv/bin/activate`
- 의존성 설치: `pip install -r requirements.txt`
- 봇 실행: `python app.py`

## 환경변수
필수:
- `BOT_TOKEN`

macOS 전용 (Opus 음성 라이브러리):
- `OPUS_PATH` — libopus 경로. Homebrew: `OPUS_PATH=/opt/homebrew/lib/libopus.dylib`
  (Apple Silicon) 또는 `/usr/local/lib/libopus.dylib` (Intel). `brew install opus`로 설치.

오디오 백엔드 선택 (`AUDIO_BACKEND`):
- `ffmpeg` (기본) — FFmpeg + yt-dlp (YouTube에 `cookies.txt` 필요)
- `lavalink` — 자체 호스팅 단일 Lavalink 서버 (Pomice)
- `hybrid` — 검색은 Lavalink, 재생은 FFmpeg/yt-dlp
- `lavalink-node` — 공개 Lavalink 노드 풀(서버 호스팅 불필요), 순서 기반 sticky failover

### `lavalink` / `hybrid` (자체 호스팅 서버) 설정
- `LAVALINK_HOST=127.0.0.1`
- `LAVALINK_PORT=2333`
- `LAVALINK_PASSWORD=youshallnotpass`
- `LAVALINK_IDENTIFIER=main`

### `lavalink-node` (공개 노드 풀) 설정
하나 또는 두 소스(REST 목록 API, 로컬 JSON 파일)에서 공개 노드를 받아, 설정된 소스를 재생할 수
있는 노드를 판별하고, 순서 기반 sticky failover로 재생한다(작동하는 노드는 실패할 때까지 유지되고,
실패하면 다음 노드로 넘어감).

노드 목록 소스 — 값이 **제공되면(비어있지 않으면)** 해당 소스를 사용한다. 둘 다 설정되면 합쳐서
host:port로 중복 제거하고, 둘 다 비면 빈 풀이 된다.
- `LAVALINK_LIST_URL=https://lavalink-list.ajieblogs.eu.org/All` — 노드 목록 API (`/SSL`, `/NonSSL`도
  가능). 기본값이 있어 기본적으로 사용됨. 비우면(`LAVALINK_LIST_URL=`) URL 소스 끔.
- `LAVALINK_NODE_LIST_FILE=` — 로컬 JSON 노드 목록 경로(기본 빈 값 = 끔; 경로를 지정해야 사용).
  목록 API와 동일 스키마 —
  `{"identifier","host","port","password","secure","version"}` 배열. 예:
  ```json
  [{"identifier":"lava-v4.ajieblogs.eu.org","host":"lava-v4.ajieblogs.eu.org","port":443,
    "password":"https://dsc.gg/ajidevserver","secure":true,"version":"v4"}]
  ```
  이 파일은 **git-ignore**된다(heavencloud.in 같은 공개 노드 페이지에서 수집한 로컬 큐레이션 목록).
  커밋된 템플릿 `lavalink_nodes.example.json`을 `lavalink_nodes.json`으로 복사해 실제 노드를 채운다.
  파일 노드도 URL 노드와 **동일한** secure-only / v4 / probe 필터를 거치므로, non-SSL 항목은
  `LAVALINK_NODE_SECURE_ONLY=false`가 아니면 제외된다.
- `LAVALINK_NODE_PROBE_TIMEOUT=8` — 전체 노드 점검(목록 fetch + 소스 probe + 노드 연결, 병렬 수행)에
  대한 총 시간 예산(초). 낮을수록 시작이 빠르지만 느린 노드는 탈락.
- `LAVALINK_NODE_PROBE_QUERY=lofi hip hop` — YouTube probe 검색어
- `LAVALINK_NODE_SECURE_ONLY=true` — secure(wss) 노드만 사용. non-secure 노드는 Discord 음성 토큰을
  평문 전송하므로 켜는 것을 권장(봇 토큰은 노드로 전송되지 않음).
- `LAVALINK_NODE_MAX_FAILOVER=3` — 한 재생/검색 요청에서 포기 전까지 시도할 최대 노드 수(모든 노드를
  연쇄 호출하는 것 방지). 새 요청은 예산이 초기화됨. `0` = 무제한.
- `LAVALINK_NODE_REFRESH_HOURS=24` — 이 주기마다 공개 노드 풀을 자동 재탐색(공개 노드는 수시로
  죽고 살아남). 재탐색은 모든 player를 파괴하므로, 어느 길드든 음성에 연결돼 "사용 중"인 주기는
  건너뛰고 다음 주기에 재시도. `0`이면 비활성(수동 `-nodes`만).
- `LAVALINK_NODE_SOURCE=youtube` — 허용 소스(아래 참고): `youtube` / `spotify` / `both`
- `LAVALINK_NODE_SPOTIFY_PROBE_URL=https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8` — 노드의
  LavaSrc 플러그인 보유 여부를 판별하는 데 쓰는 Spotify 트랙 URL(소스에 Spotify가 포함될 때만 사용).

활성 모드의 소스 probe(들)와 pomice `/version` 점검을 **모두** 통과한 노드만 사용한다. 불안정한
노드는 자동으로 제외되고 재시작 / `-nodes` 시 재평가된다.

#### 소스 모드 (`LAVALINK_NODE_SOURCE`)
공개 노드에서의 Spotify 재생은 **노드의 LavaSrc 플러그인이 서버사이드에서** 처리한다 — 봇이 Spotify
URL을 노드의 `loadtracks`로 보내면 LavaSrc가 해석/미러링한다(봇에 Spotify API 자격증명 불필요).
모든 공개 노드가 LavaSrc를 갖춘 건 아니므로, 봇이 각 노드의 Spotify 지원 여부를 probe해 가능한
노드만 유지한다. **Spotify는 명시적 Spotify URL(트랙/앨범/플레이리스트/아티스트 링크)일 때만 재생**되며,
일반 텍스트 검색은 절대 Spotify로 가지 않는다.

- `youtube` (기본) — YouTube만. Spotify URL을 붙이면
  "스포티파이는 현재 모드에서 제공할 수 없습니다." 응답 후 자동 삭제. (기존과 동일.)
- `spotify` — Spotify URL만. YouTube 링크는 "유튜브는 현재 모드에서 제공할 수 없습니다.",
  일반 텍스트 검색은 "현재 모드에서는 Spotify 링크만 재생할 수 있어요." 응답 후 둘 다 자동 삭제.
- `both` — Spotify URL 재생 + YouTube 링크/텍스트 검색 정상 동작. 단 `both`는 *두* 소스를 모두
  지원하는 노드만 유지하므로 healthy 풀이 작아질 수 있다.

> Spotify 앨범/플레이리스트/아티스트 URL은 전체 트랙을 큐에 추가한다(Spotify 한정). YouTube
> 플레이리스트는 기존대로 첫 곡만 추가된다.

## 명령어
- `-reload [cog]` (오너) — cog 리로드
- `-nodes` (오너) — 공개 노드 풀 재탐색 (`AUDIO_BACKEND=lavalink-node`에서만 의미 있음)

## 로컬 테스트 (Discord 불필요)
- `python test/lavalink_node_test.py` — 공개 노드 discover/probe, sticky failover 실행, yt-dlp + ffplay로
  로컬 재생(Discord 음성 대체물; Lavalink는 로컬 스피커로 출력 불가). 경로로 실행할 것;
  `python -m test.lavalink_node_test`는 안 됨(루트 `test.py`가 `test` 패키지를 가림).
- `python test/lavalink_node_spotify_test.py` — 각 공개 노드의 YouTube/Spotify(LavaSrc) 지원을 순수
  REST로 probe(Discord/pomice 불필요)하고, 노드별 표와 `LAVALINK_NODE_SOURCE` 모드별 예상 healthy 풀
  크기를 출력. Spotify URL을 입력해 실제 resolve도 확인 가능. 봇을 전환하기 전에 현재 공개 노드
  목록에서 `spotify`/`both` 모드가 동작 가능한지 점검하는 용도. 경로로 실행(동일한 `test.py` 셰도잉 주의).

## 메모
- 명령 UX는 기존 유지; 모든 재생 로직은 `core/audio`와 `AudioService`를 통한다.
- 노드 discover + sticky failover는 `core/audio/lavalink_node_pool.py`에 있다(Discord/pomice 무의존);
  `lavalink-node` 백엔드(`core/audio/lavalink_node_backend.py`)가 풀 위에서 pomice player를 구동한다.
