import os

AUDIO_BACKEND = os.getenv("AUDIO_BACKEND", "ffmpeg").strip().lower()

LAVALINK_HOST = os.getenv("LAVALINK_HOST", "127.0.0.1")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
LAVALINK_IDENTIFIER = os.getenv("LAVALINK_IDENTIFIER", "main")

# --- lavalink-node 엔진: 공개 노드 목록(DarrenOfficial/lavalink-list)을 받아와 풀로 운영 ---
# 공식 REST API(10분마다 갱신, 온라인 노드만). /All, /SSL, /NonSSL 제공.
LAVALINK_LIST_URL = os.getenv("LAVALINK_LIST_URL", "https://lavalink-list.ajieblogs.eu.org/All")
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
