# create_audio_service / AudioService 는 discord 등 무거운 의존을 끌어온다.
# 패키지 import 시점이 아니라 실제 접근 시점에 lazy 로딩하여,
# core.audio.lavalink_node_pool 같은 순수 모듈을 discord 없이 단독 import 할 수 있게 한다.
# (로컬 단일 클라 테스트가 디스코드 라이브러리 없이 동작하기 위함)


def __getattr__(name):  # PEP 562
    if name == "create_audio_service":
        from .factory import create_audio_service

        return create_audio_service
    if name == "AudioService":
        from .service import AudioService

        return AudioService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
