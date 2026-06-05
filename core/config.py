import os

AUDIO_BACKEND = os.getenv("AUDIO_BACKEND", "ffmpeg").strip().lower()

LAVALINK_HOST = os.getenv("LAVALINK_HOST", "127.0.0.1")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_IDENTIFIER = os.getenv("LAVALINK_IDENTIFIER", "main")

# --- lavalink-node 엔진: 공개 노드 목록을 받아와 풀로 운영 ---
# 노드 목록 소스는 "제공된 것"을 사용한다(값이 비어있지 않으면 사용). 둘 다 제공되면 합쳐서
# (host,port) 중복제거, 둘 다 비면 빈 풀. secure-only/v4/probe 필터는 두 소스에 동일 적용.
# 두 소스 모두 기본값 없음(미설정/빈값 = off) → URL/FILE 대칭. 최소 하나는 지정해야 노드가 잡힌다.
#
# LAVALINK_NODE_LIST_URL: DarrenOfficial/lavalink-list REST API(10분마다 갱신, 온라인 노드만).
#   /All, /SSL, /NonSSL 제공. 권장값: https://lavalink-list.ajieblogs.eu.org/All (.env / .env.example 참고).
LAVALINK_NODE_LIST_URL = os.getenv("LAVALINK_NODE_LIST_URL", "").strip()
# LAVALINK_NODE_LIST_FILE: 로컬 노드 목록 JSON 경로. lavalink-list API 와 동일 스키마의 배열:
#   [{"identifier","host","port","password","secure","version"}, ...]
LAVALINK_NODE_LIST_FILE = os.getenv("LAVALINK_NODE_LIST_FILE", "").strip()
# 각 노드의 YouTube 재생 가능 여부를 판별할 때 쓰는 loadtracks 요청 타임아웃(초)
LAVALINK_NODE_PROBE_TIMEOUT = float(os.getenv("LAVALINK_NODE_PROBE_TIMEOUT", "8"))
# 판별용 가벼운 고정 검색어 (ytsearch: 접두사 자동 부여)
LAVALINK_NODE_PROBE_QUERY = os.getenv("LAVALINK_NODE_PROBE_QUERY", "lofi hip hop")
# secure(wss/https) 노드만 사용할지 여부.
#   봇이 디스코드 음성 연결 자격증명(endpoint/token/session)을 노드에 넘기는데,
#   non-secure(http/ws) 노드는 이 토큰을 평문 전송하므로 도청 위험이 있다.
#   실제 음성 송출(Phase 3)/운영에서는 true 권장. 로컬 loadtracks 테스트만 할 땐 false 로 풀을 넓힐 수 있음.
LAVALINK_NODE_SECURE_ONLY = os.getenv("LAVALINK_NODE_SECURE_ONLY", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# 한 번의 재생/검색 요청에서 failover 로 시도할 최대 노드 수.
#   과도한 재시도(여러 노드 연쇄 호출 → DDoS 유사)를 막는 상한. 0 이하면 무제한(healthy 전체).
LAVALINK_NODE_MAX_FAILOVER = int(os.getenv("LAVALINK_NODE_MAX_FAILOVER", "3"))

# 허용 소스 모드. youtube(기본) / spotify / both.
#   - youtube : YouTube 검색·URL만. Spotify URL 은 차단(안내 후 자동삭제).
#   - spotify : Spotify URL 만. YouTube 링크/텍스트 검색은 차단.
#   - both    : Spotify URL + YouTube 검색·URL 둘 다.
#   Spotify 재생은 "노드의 LavaSrc 플러그인"이 서버사이드에서 해석한다(클라 자격증명 불필요).
#   노드는 모드별로 probe 해 해당 소스를 실제 처리하는 노드만 사용한다. both 는 둘 다 통과한 노드만.
LAVALINK_NODE_SOURCE = os.getenv("LAVALINK_NODE_SOURCE", "youtube").strip().lower()
if LAVALINK_NODE_SOURCE not in ("youtube", "spotify", "both"):
    LAVALINK_NODE_SOURCE = "youtube"
# 노드의 Spotify(LavaSrc) 지원 여부 판별에 쓰는 안정적인 공개 Spotify 트랙 URL.
#   source 가 spotify/both 일 때만 사용된다.
LAVALINK_NODE_SPOTIFY_PROBE_URL = os.getenv(
    "LAVALINK_NODE_SPOTIFY_PROBE_URL",
    "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8",
).strip()
# 공개 노드 풀 자동 재탐색 주기(시간). 공개 노드는 수시로 죽고 살아나므로 주기적으로 갱신한다.
#   재탐색은 모든 player 를 파괴하므로, 어느 길드든 음성에 연결돼 "사용 중"이면 그 주기는 건너뛴다.
#   0 이하면 자동 재탐색 비활성( -nodes 수동 갱신만 ).
LAVALINK_NODE_REFRESH_HOURS = float(os.getenv("LAVALINK_NODE_REFRESH_HOURS", "24"))
