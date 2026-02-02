# 🎵 Music Bot Architecture & Lavalink Integration Guide

> 목적:  
> 이 문서는 현재 Discord Music Bot 레포지토리를 **유지보수 가능한 구조로 리팩터링**하고,  
> **Lavalink 기반 오디오 백엔드**를 자연스럽게 통합하기 위한 개발 지침서이다.  
> Codex(또는 AI 개발 에이전트)는 이 문서를 **최우선 설계 기준**으로 따른다.

---

## 1. 핵심 원칙 (반드시 지킬 것)

1. **cogs는 오디오 엔진을 절대 직접 다루지 않는다**
2. **재생 엔진(FFmpeg / Lavalink)은 언제든 교체 가능해야 한다**
3. **길드별 상태(큐, 현재 곡, 반복 상태)는 core에서만 관리한다**
4. **views / embeds는 상태를 “그리기만” 한다**
5. Lavalink 도입 시, 기존 명령/버튼 UX는 최대한 유지한다

---

## 2. 디렉토리 구조 (권장)

```text
core/
 ├─ audio/
 │   ├─ backend.py          # AudioBackend 추상 인터페이스
 │   ├─ lavalink_backend.py # Lavalink(Pomice) 구현체
 │   ├─ models.py           # Track, QueueItem, RepeatMode
 │   └─ service.py          # AudioService (길드별 상태/로직)
 │
 ├─ config.py               # 환경변수 / 설정
 ├─ errors.py               # 사용자 노출용 에러 정의
 └─ logger.py               # 구조화 로깅

cogs/
 └─ music.py                # /play /pause /skip 등 명령어 (로직 X)

views/
 └─ music_controls.py       # 버튼 UI (service 호출만)

embeds/
 └─ now_playing.py          # 상태 렌더링 전용

app.py                      # 봇 진입점
```

---

## 3. AudioBackend 인터페이스 (필수)

`core/audio/backend.py`

```python
from abc import ABC, abstractmethod

class AudioBackend(ABC):

    @abstractmethod
    async def connect(self, bot): ...

    @abstractmethod
    async def ensure_player(self, guild_id: int): ...

    @abstractmethod
    async def search(self, query: str): ...

    @abstractmethod
    async def play(self, guild_id: int, track): ...

    @abstractmethod
    async def stop(self, guild_id: int): ...

    @abstractmethod
    async def pause(self, guild_id: int): ...

    @abstractmethod
    async def resume(self, guild_id: int): ...

    @abstractmethod
    async def skip(self, guild_id: int): ...

    @abstractmethod
    async def set_volume(self, guild_id: int, volume: int): ...
```

> ⚠️ 주의  
> - `cogs/` 에서 backend 객체를 직접 참조하면 안 된다  
> - backend는 반드시 `AudioService` 내부에서만 사용한다

---

## 4. AudioService (가장 중요)

`core/audio/service.py`

역할:
- 길드별 플레이어 상태 관리
- 큐 / 반복 / 셔플 로직
- 트랙 종료 이벤트 처리
- 동시성 보호 (guild 단위 Lock)

필수 조건:
- `guild_id -> AudioState` 맵 유지
- 모든 큐 변경은 `asyncio.Lock` 내부에서 처리
- Lavalink 이벤트(TrackEnd)는 service가 직접 처리

```python
class AudioService:
    def __init__(self, backend: AudioBackend):
        self.backend = backend
        self.states = {}  # guild_id -> AudioState
```

---

## 5. Track / Queue 모델 표준화

`core/audio/models.py`

```python
from dataclasses import dataclass
from enum import Enum

class RepeatMode(Enum):
    NONE = 0
    ONE = 1
    ALL = 2

@dataclass
class Track:
    title: str
    uri: str
    duration: int
    requester_id: int
    source: str   # youtube / soundcloud / etc
```

> Lavalink Track 객체를 그대로 외부로 노출하지 말 것  
> → 내부 표준 Track 모델로 변환해서 사용

---

## 6. Cogs 작성 규칙 (절대 어기지 말 것)

- Cogs는 **명령 파싱 + AudioService 호출만** 한다
- 큐, 플레이어, 트랙 종료 처리 금지
- 상태 조회는 `service.get_status(guild_id)` 형태로만

❌ 금지 예시:
```python
player.queue.append(...)
```

✅ 허용 예시:
```python
await audio_service.enqueue_and_play(ctx.guild.id, query, ctx.author.id)
```

---

## 7. Views / Embeds 규칙

### Views
- 버튼 콜백에서는 service 메서드만 호출
- backend, player 직접 접근 금지

### Embeds
- service에서 반환한 DTO만 사용
- 계산/로직 금지

---

## 8. Lavalink 통합 규칙

- Lavalink v4 사용
- Pomice 클라이언트 권장
- YouTube 사용 시 youtube-source 플러그인 전제
- Lavalink 노드 정보는 env 기반 설정

`.env.example`

```env
DISCORD_TOKEN=...
LAVALINK_HOST=127.0.0.1
LAVALINK_PORT=2333
LAVALINK_PASSWORD=youshallnotpass
```

---

## 9. 단계별 적용 전략 (중요)

1. **현재 재생 엔진 유지한 채 AudioService 구조부터 도입**
2. Cogs / Views / Embeds를 service 기반으로 리팩터링
3. LavalinkBackend 추가
4. backend 교체 (코드 수정 최소화)
5. seek / filter / autoplay 등 고급 기능 추가

> ❗ backend 교체는 반드시 “한 단계 뒤”에 진행한다

---

## 10. 금지 사항 요약

- ❌ cogs에서 플레이어/큐 직접 조작
- ❌ Lavalink 객체를 embed/view에 전달
- ❌ guild 상태를 전역 변수로 흩뿌리기
- ❌ 버튼 콜백에서 로직 처리

---

## 11. 최종 목표 상태

- 오디오 엔진 교체 가능
- Lavalink 도입에도 UX 유지
- 길드별 상태 버그 없음
- 코드 읽는 즉시 구조가 보임
- AI(Codex)가 유지보수 가능

---

**Codex는 이 문서를 기준으로 리팩터링 및 신규 코드 작성을 수행한다.**
