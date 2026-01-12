import discord


def get_music_view(is_paused: bool = False, loop_enabled: bool = False) -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="⏹️ 정지", style=discord.ButtonStyle.red, custom_id="stop"))
    if is_paused:
        view.add_item(discord.ui.Button(label="▶️ 재개", style=discord.ButtonStyle.blurple, custom_id="resume"))
    else:
        view.add_item(discord.ui.Button(label="⏸️ 일시정지", style=discord.ButtonStyle.blurple, custom_id="pause"))
    view.add_item(discord.ui.Button(label="⏭️ 스킵", style=discord.ButtonStyle.green, custom_id="skip"))
    loop_style = discord.ButtonStyle.green if loop_enabled else discord.ButtonStyle.gray
    view.add_item(discord.ui.Button(label="🔁 반복", style=loop_style, custom_id="loop"))
    view.add_item(discord.ui.Button(label="🔀 셔플", style=discord.ButtonStyle.gray, custom_id="shuffle"))
    return view
