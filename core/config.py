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
# YouTube 재생 판별(probe)을 생략하고 항상 healthy 로 취급할 호스트(콤마 구분).
#   "항상 되는" 노드를 매번 판별하느라 시간 쓰지 않기 위함. 실제 재생 가능 여부는
#   play 단계의 failover 가 검증한다(loadtracks/play 실패 시 자동 제외).
LAVALINK_NODE_PROBE_EXEMPT_HOSTS = [
    h.strip()
    for h in os.getenv("LAVALINK_NODE_PROBE_EXEMPT_HOSTS", "lavalinkv4.serenetia.com").split(",")
    if h.strip()
]
