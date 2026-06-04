"""음악 메시지 UI (Discord Components V2 / LayoutView).

기존 embed(`embeds/music_embed.py`) + 버튼 View(`views/music_view.py`)를 대체한다.
LayoutView 메시지는 content/embed 와 공존할 수 없으므로 모든 텍스트는 TextDisplay 로 구성한다.
버튼 custom_id 는 기존과 동일(stop/pause/resume/skip/loop/shuffle) — cog 의 on_interaction 라우팅 재사용.
"""

from __future__ import annotations

import re
from typing import Optional

import discord
from discord import ui

ACCENT = 0x00FF00

# 마스크 링크 `[텍스트](url)` 의 텍스트 안에 이모지가 있으면 하이퍼링크가 깨진다.
# 클릭 텍스트에서 이모지를 제거하기 위한 패턴(🎸 등 주요 이모지/그림문자 영역).
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # 주요 이모지/그림문자 (🎸 등)
    "\U00002600-\U000027BF"   # 기타 기호 + 딩벳
    "\U00002B00-\U00002BFF"   # 기타 기호/화살표
    "\U0000FE00-\U0000FE0F"   # variation selector
    "\U0000200D"              # zero-width joiner
    "\U000020E3"              # keycap
    "]+"
)


def _link_text(title: str) -> str:
    """마스크 링크 텍스트용으로 제목에서 이모지를 제거한다(이모지가 있으면 하이퍼링크가 깨짐).

    이모지만으로 된 제목이면 빈 문자열을 반환한다(호출부에서 일반 텍스트로 폴백).
    """
    cleaned = _EMOJI_RE.sub("", title)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _build_action_row(*, is_paused: bool, is_loop: bool) -> ui.ActionRow:
    row = ui.ActionRow()
    row.add_item(ui.Button(label="정지", style=discord.ButtonStyle.red, custom_id="stop"))
    if is_paused:
        row.add_item(ui.Button(label="재개", style=discord.ButtonStyle.blurple, custom_id="resume"))
    else:
        row.add_item(ui.Button(label="일시정지", style=discord.ButtonStyle.blurple, custom_id="pause"))
    row.add_item(ui.Button(label="스킵", style=discord.ButtonStyle.green, custom_id="skip"))
    loop_style = discord.ButtonStyle.green if is_loop else discord.ButtonStyle.gray
    row.add_item(ui.Button(label="반복", style=loop_style, custom_id="loop"))
    row.add_item(ui.Button(label="셔플", style=discord.ButtonStyle.gray, custom_id="shuffle"))
    return row


def build_now_playing_view(
    *,
    user_name: str,
    user_icon: str,
    music_title: str,
    music_thumbnail: str,
    music_url: str,
    is_paused: bool = False,
    is_loop: bool = False,
    queue_preview: Optional[str] = None,
) -> ui.LayoutView:
    """재생 중/일시정지 카드(배너형). 앨범아트는 MediaGallery 로 크게, 요청자 아바타는 Section 썸네일."""
    header = "음악 일시 중지됨" if is_paused else "음악 재생중"
    # 마스크 링크 텍스트는 이모지를 제거한 제목. 제목이 이모지뿐이라 비면 링크가 깨지므로
    # 원제목(이모지 포함)을 일반 텍스트로 폴백한다(빈 텍스트/깨진 링크 둘 다 방지).
    link_label = _link_text(music_title)
    if music_url and link_label:
        link = f"[{link_label}]({music_url})"
    else:
        link = music_title or "Unknown"
    header_text = f"## {header}\n{link}"

    container = ui.Container(accent_colour=ACCENT)

    # 헤더 + 요청자 아바타(작은 썸네일). user_icon 없으면 텍스트만.
    container.add_item(ui.TextDisplay(header_text))

    # 곡 썸네일(크게). 없으면 생략.
    if music_thumbnail:
        gallery = ui.MediaGallery()
        gallery.add_item(media=music_thumbnail)
        container.add_item(gallery)

    # 푸터 subtext (요청자 · 상태 · 반복)
    state = "일시정지" if is_paused else "재생중"
    loop_txt = "켜짐" if is_loop else "꺼짐"
    container.add_item(ui.TextDisplay(f"-# {user_name} · {state} · 반복 {loop_txt}"))

    container.add_item(ui.Separator())

    if queue_preview:
        container.add_item(ui.TextDisplay(f"**대기열**\n{queue_preview}"))

    container.add_item(_build_action_row(is_paused=is_paused, is_loop=is_loop))

    view = ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


def build_idle_view() -> ui.LayoutView:
    """대기 상태 카드(기존 music_stop_embed 대체). 버튼 없음."""
    container = ui.Container(
        ui.TextDisplay("### 음악 재생을 기다리고 있어요."),
        accent_colour=ACCENT,
    )
    view = ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
