import asyncio
import random
from typing import Dict, List, Optional, Sequence, Union

import discord
from discord.ext import commands
from discord import app_commands, Interaction, FFmpegPCMAudio, Embed, Attachment, File, AllowedMentions, VoiceState
from discord.ext.commands import CommandError
from discord.ui import View
from discord.utils import MISSING

from core.util import log_event
from core.local.music import MusicDataSource
from core.local.music.model import MusicModel
from core.model.music_application import MusicApplication
from core.network import YoutubePlaylist, YoutubeSearch
from core.network.youtube.youtube_service import YoutubeService
from core.type import MusicType
from embeds.music_embed import music_play_embed, music_pause_embed, music_stop_embed
from views import get_music_view

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Key: guild_id, Value: MusicType
        self.music_queue: Dict[int, MusicType] = {}
        # key: guild_id, Value: channel_id
        self.guild_channel: Dict[int, MusicModel] = {}
        asyncio.run_coroutine_threadsafe(self.load_local_guild_channel(), self.bot.loop)

    def build_queue_preview(self, queue: List[MusicApplication], max_items: int = 5) -> Optional[str]:
        if not queue:
            return None
        lines = [f"{idx + 1}. {item.youtube_search.title}" for idx, item in enumerate(queue[:max_items])]
        if len(queue) > max_items:
            lines.append(f"... 외 {len(queue) - max_items}곡")
        return "\n".join(lines)

    async def refresh_now_playing_embed(self, guild_id: int, *, is_paused: bool = False):
        voice_model = self.music_queue.get(guild_id)
        if voice_model is None:
            return
        music = voice_model.get("now_playing")
        if music is None:
            return
        if is_paused:
            embed = music_pause_embed(
                user_name=music.user_name,
                user_icon=music.user_icon,
                music_title=music.youtube_search.title,
                music_thumbnail=music.youtube_search.thumbnail_url,
                music_url=music.youtube_search.video_url,
                isLoop=voice_model.get("loop"),
            )
        else:
            embed = music_play_embed(
                user_name=music.user_name,
                user_icon=music.user_icon,
                music_title=music.youtube_search.title,
                music_thumbnail=music.youtube_search.thumbnail_url,
                music_url=music.youtube_search.video_url,
                isLoop=voice_model.get("loop"),
            )
        queue_preview = self.build_queue_preview(voice_model["queue"])
        if queue_preview:
            embed.add_field(name="대기열", value=queue_preview, inline=False)
        await self.music_message_edit(
            guild_id=guild_id,
            embed=embed,
            view=get_music_view(is_paused=is_paused, loop_enabled=voice_model.get("loop", False))
        )

    async def load_local_guild_channel(self):
        local_default_channels = await MusicDataSource.get_all()
        for i in local_default_channels:
            self.guild_channel[i.guild_id] = i
        log_event("로컬에서 Music 채널 불러옴.")
        log_event(f"guild_channel={self.guild_channel}")

    def guild_channel_ids(self) -> List[int]:
        return [model.channel_id for model in self.guild_channel.values()]

    async def get_channel_message(self, guild_id: int) -> discord.Message:
        music_model = self.guild_channel.get(guild_id, None)
        return await (await self.bot.fetch_channel(music_model.channel_id)).fetch_message(music_model.message_id)


    def check_voice_play(self, ctx: commands.Context | Interaction):
        """
        해당 함수는 보이스의 설정과 유저의 보이스 입장을 선별하는 데코레이터 함수입니다.
        :raise CommandError:
        :return:
        """
        if self.music_queue.get(ctx.guild.id) is None:
            raise CommandError("음악이 재생되고 있지 않아요!")

        if isinstance(ctx, Interaction):
            if ctx.user.voice is None:
                raise CommandError("음성 채널에 먼저 입장해주세요!")
        else:
            if ctx.author.voice is None:
                raise CommandError("음성 채널에 먼저 입장해주세요!")

    @commands.command("반복", aliases=["loop"])
    async def loop(self, ctx: commands.Context):
        message = await self._loop(ctx)
        await ctx.send(message)

    @commands.command("셔플", aliases=["shuffle"])
    async def shuffle(self, ctx: commands.Context):
        message = await self._shuffle(ctx)
        await ctx.send(message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: VoiceState, after: VoiceState):
        # 봇이 음성 채널을 나갔을 경우
        if member.id == self.bot.user.id:
            if after.channel is None:
                log_event("나가기1")
                return await self.clear_guild_queue(member.guild.id)

            # 유저 없는 채널로 이동한 경우
            if sum(1 for m in after.channel.members if not m.bot) == 0:
                log_event("나가기2")
                return await self.clear_guild_queue(member.guild.id)

            # 봇의 채널 이동 처리
            if after.channel is not None:
                queue = self.music_queue.get(member.guild.id)
                if queue is not None:
                    queue["voice_channel_id"] = after.channel.id
            return

        # 일반 유저가 채널을 나간 경우
        if before.channel is not None:
            join_member_list = before.channel.members
            if (
                    self.bot.user.id in [m.id for m in join_member_list] and
                    sum(1 for m in join_member_list if not m.bot) == 0
            ):
                log_event("나가기3")
                return await self.clear_guild_queue(member.guild.id)

    async def play_music(
        self,
        guild_id: int, 
        beforeMusic: Optional[MusicApplication] = None,
    ):
        voice_model = self.music_queue[guild_id]
        
        if voice_model["loop"] and beforeMusic != None:
            voice_model["queue"].append(beforeMusic)

        if len(voice_model["queue"]) == 0:
            voice_model["now_playing"] = None
            message = await self.get_channel_message(guild_id)
            return await message.edit(
                embed=music_stop_embed(),
                view=None
            )

        music = voice_model["queue"].pop(0)
        source = FFmpegPCMAudio(music.youtube_search.audio_source, **FFMPEG_OPTIONS)
        log_event(music.youtube_search.audio_source)
        voice_model["now_playing"] = music
        voice_model["vc"].play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(self.play_music(guild_id, music), self.bot.loop),
        )
        await self.refresh_now_playing_embed(guild_id, is_paused=False)

    async def clear_guild_queue(self, guild_id: int):
        if self.music_queue.get(guild_id) is not None:
            self.music_queue[guild_id]["vc"].stop()
            await self.music_queue[guild_id]["vc"].disconnect()
            await self.music_message_edit(
                guild_id=guild_id,
                embed=music_stop_embed(),
                view=None,
            )
        self.music_queue.pop(guild_id, None)

    async def music_message_edit(
        self,
        *,
        guild_id: int,
        content: Optional[str] = MISSING,
        embed: Optional[Embed] = MISSING,
        embeds: Sequence[Embed] = MISSING,
        attachments: Sequence[Union[Attachment, File]] = MISSING,
        suppress: bool = False,
        delete_after: Optional[float] = None,
        allowed_mentions: Optional[AllowedMentions] = MISSING,
        view: Optional[View] = MISSING,
    ):
        try:
            message = await self.get_channel_message(guild_id)
            await message.edit(
                content=content,
                embed=embed,
                embeds=embeds,
                attachments=attachments,
                suppress=suppress,
                delete_after=delete_after,
                allowed_mentions=allowed_mentions,
                view=view
            )
        except discord.NotFound:
            try:
                channel = await self.bot.fetch_channel(self.guild_channel[guild_id].channel_id)
                new_message = await channel.send(
                    content=content,
                    embed=embed,
                    embeds=embeds,
                    attachments=attachments,
                    suppress_embeds=suppress,
                    delete_after=delete_after,
                    allowed_mentions=allowed_mentions,
                    view=view
                )
                self.guild_channel[guild_id].message_id = new_message.id
                await MusicDataSource.update_message_id(
                    guild_id=guild_id,
                    message_id=new_message.id,
                )
            except:
                del self.guild_channel[guild_id]
                await MusicDataSource.delete(guild_id)

    async def _pause(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)

        if not self.music_queue[ctx.guild.id]["vc"].is_playing():
            raise CommandError("현재 음악을 재생하고 있지 않아요")
        
        self.music_queue[ctx.guild.id]["vc"].pause()
        await self.refresh_now_playing_embed(ctx.guild.id, is_paused=True)

    async def _resume(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)
        self.music_queue[ctx.guild.id]["vc"].resume()
        await self.refresh_now_playing_embed(ctx.guild.id, is_paused=False)

    async def _stop(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)
        await self.clear_guild_queue(ctx.guild.id)

    async def _skip(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)
        self.music_queue[ctx.guild.id]["vc"].stop()

    async def _loop(self, ctx: commands.Context | Interaction) -> str:
        self.check_voice_play(ctx)
        guild_queue = self.music_queue[ctx.guild.id]
        guild_queue["loop"] = not guild_queue["loop"]
        await self.refresh_now_playing_embed(
            ctx.guild.id,
            is_paused=guild_queue["vc"].is_paused()
        )
        state = "켜짐" if guild_queue["loop"] else "꺼짐"
        return f"반복을 {state}으로 설정했어요."

    async def _shuffle(self, ctx: commands.Context | Interaction) -> str:
        self.check_voice_play(ctx)
        guild_queue = self.music_queue[ctx.guild.id]
        if len(guild_queue["queue"]) < 2:
            raise CommandError("셔플할 곡이 부족해요.")
        random.shuffle(guild_queue["queue"])
        await self.refresh_now_playing_embed(
            ctx.guild.id,
            is_paused=guild_queue["vc"].is_paused()
        )
        return "대기열을 섞었어요."

    @app_commands.command(name="채널설정")
    async def set_channel(self, interaction: Interaction, channel: discord.TextChannel):
        message = await channel.send(embed=music_stop_embed())

        if await MusicDataSource.get(interaction.guild_id) is not None:
            await MusicDataSource.update(
                guild_id=interaction.guild.id,
                message_id=message.id,
                channel_id=channel.id,
            )
        else:
            await MusicDataSource.insert(
                guild_id=interaction.guild.id,
                channel_id=channel.id,
                message_id=message.id
            )

        self.guild_channel[interaction.guild.id] = MusicModel(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            message_id=message.id,
        )
        await interaction.response.send_message(f"성공적으로 채널을 설정하였습니다!")


    @commands.Cog.listener()
    async def on_interaction(self, interaction: Interaction):
        try:
            if interaction.client.user.id != self.bot.user.id and interaction.type != discord.InteractionType.component:
                return
            log_event(interaction.message)
            async def send_and_delete_message(content: str):
                await interaction.response.send_message(content, delete_after=5)

            custom_id = interaction.data["custom_id"]
            if custom_id == "stop":
                await self._stop(interaction)
                return await send_and_delete_message("음악 재생을 초기화했어요")
            elif custom_id == "pause":
                await self._pause(interaction)
                return await send_and_delete_message("음악 재생을 일시정지 했어요")
            elif custom_id == "resume":
                await self._resume(interaction)
                return await send_and_delete_message("음악 재생을 재개 했어요")
            elif custom_id == "skip":
                await self._skip(interaction)
                return await send_and_delete_message("음악 재생을 넘겼어요")
            elif custom_id == "loop":
                message = await self._loop(interaction)
                return await send_and_delete_message(message)
            elif custom_id == "shuffle":
                message = await self._shuffle(interaction)
                return await send_and_delete_message(message)



            await interaction.response.send_message(f"{interaction.data['custom_id']} 버튼이 클릭되었습니다!")
        except Exception as exception:
            if isinstance(exception, CommandError):
                return await interaction.response.send_message(f"{exception.args[0]}", ephemeral=True)
            raise exception

    @commands.command("일시정지")
    async def pause(self, ctx: commands.Context):
        await self._pause(ctx)
        await ctx.send("음악 재생을 일시정지 했어요")

    @commands.command("재개")
    async def resume(self, ctx: commands.Context):
        await self._resume(ctx)
        await ctx.send("음악 재생을 재개 했어요")

    @commands.command("정지")
    async def stop(self, ctx: commands.Context):
        await self._stop(ctx)
        await ctx.send("음악 재생을 초기화했어요")

    @commands.command("스킵")
    async def skip(self, ctx: commands.Context):
        await self._skip(ctx)
        await ctx.send("음악 재생을 넘겼어요")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.bot.process_commands(message)
        if (
                message.author.bot or
                isinstance(message.channel, discord.channel.DMChannel) or
                message.channel.id not in self.guild_channel_ids()
        ): return

        if message.author.voice is None:
            return

        if self.music_queue.get(message.guild.id, None) is None:
            log_event("나가기4")
            await self.clear_guild_queue(message.guild.id)

            if not message.guild.voice_client:
                vc = await message.author.voice.channel.connect()
            else:
                vc = message.guild.voice_client

            self.music_queue[message.guild.id] = {
                "guild_id": message.guild.id,
                "voice_channel_id": message.author.voice.channel.id,
                "vc": vc,
                "volume": 1.0,
                "queue": [],
                "loop": False,
            }

        guild_queue = self.music_queue[message.guild.id]
        if message.author.voice.channel.id != guild_queue["voice_channel_id"]:
            return

        async def delete_message():
            try:
                await message.delete(delay=5)
            except:
                pass

        await delete_message()

        search_result = await YoutubeService.search(message.content)

        async def send_and_delete_message(content: str):
            await message.channel.send(content, delete_after=5)

        if search_result is None:
            await send_and_delete_message("노래를 찾지 못했어요..")

        def youtube_play_list_to_music_application(play_list: YoutubeSearch) -> MusicApplication:
            return MusicApplication(
                youtube_search=play_list,
                user_id=message.author.id,
                user_name=message.author.name,
                user_icon=message.author.display_avatar.url,
            )

        if isinstance(search_result, YoutubePlaylist):
            guild_queue["queue"].extend(list(map(lambda x: youtube_play_list_to_music_application(x), search_result.songs)))
            await send_and_delete_message(f"{search_result.title} 플레이 리스트를 추가했어요!\n추가된 곡 수: {search_result.song_cnt}")
        else:
            guild_queue["queue"].append(youtube_play_list_to_music_application(search_result))
            await send_and_delete_message(f"{search_result.title} 곡을 추가했어요!")

        if not guild_queue["vc"].is_playing():
            await self.play_music(message.guild.id)
        else:
            await self.refresh_now_playing_embed(
                message.guild.id,
                is_paused=guild_queue["vc"].is_paused()
            )












async def setup(bot):
    await bot.add_cog(Music(bot))
