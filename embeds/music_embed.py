import discord


def music_base_embed(
    title: str,
    user_icon: str,
    user_name: str,
    music_title: str,
    music_thumbnail: str,
    music_url: str,
    isPlaying: bool,
    isLoop: bool,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=0x00FF00)
    embed.description = f"[{music_title}]({music_url})"
    embed.set_thumbnail(url=user_icon)
    embed.set_image(url=music_thumbnail)
    embed.set_footer(
        text="{0} · {1} · 반복 {2}".format(user_name, "재생중" if isPlaying else "일시정지", "켜짐" if isLoop else "꺼짐"), 
        icon_url=user_icon
    )
    return embed

def music_pause_embed(
    user_icon: str,
    user_name: str,
    music_title: str,
    music_thumbnail: str,
    music_url: str,
    isLoop: bool,
) -> discord.Embed:
    return music_base_embed(
        title="음악 일시 중지됨",
        user_icon=user_icon,
        user_name=user_name,
        music_title=music_title,
        music_thumbnail=music_thumbnail,
        music_url=music_url,
        isPlaying=False,
        isLoop=isLoop,
    )

def music_play_embed(
    user_icon: str,
    user_name: str,
    music_title: str,
    music_thumbnail: str,
    music_url: str,
    isLoop: bool,
) -> discord.Embed:
    return music_base_embed(
        title="음악 재생중",
        user_icon=user_icon,
        user_name=user_name,
        music_title=music_title,
        music_thumbnail=music_thumbnail,
        music_url=music_url,
        isPlaying=True,
        isLoop=isLoop,
    )

def music_stop_embed() -> discord.Embed:
    return discord.Embed(title="음악 재생을 기다리고 있어요.", color=0x00FF00)