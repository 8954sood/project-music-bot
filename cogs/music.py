import asyncio
from typing import Dict, List, Optional, Sequence, Union

import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed, Attachment, File, AllowedMentions, VoiceState
from discord.ext.commands import CommandError
from discord.ui import View
from discord.utils import MISSING

from core.audio import create_audio_service
from core.util import log_event
from core.local.music import MusicDataSource
from core.local.music.model import MusicModel
from core.model.music_application import MusicApplication
from core.network import YoutubePlaylist, YoutubeSearch
from core.network.youtube.youtube_service import YoutubeService
from embeds.music_embed import music_play_embed, music_pause_embed, music_stop_embed
from views import get_music_view
MAX_GUILD_ACTION_PENDING = 5

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # key: guild_id, Value: channel_id
        self.guild_channel: Dict[int, MusicModel] = {}
        # Key: guild_id, Value: {"lock": asyncio.Lock, "pending": int}
        self.guild_action_state: Dict[int, Dict[str, object]] = {}
        self.audio_service = create_audio_service(bot)
        self.audio_service.on_track_start = self._on_track_start
        self.audio_service.on_queue_empty = self._on_queue_empty
        asyncio.run_coroutine_threadsafe(self.load_local_guild_channel(), self.bot.loop)

    def build_queue_preview(self, queue: List[MusicApplication], max_items: int = 5) -> Optional[str]:
        if not queue:
            return None
        lines = [f"{idx + 1}. {item.youtube_search.title}" for idx, item in enumerate(queue[:max_items])]
        if len(queue) > max_items:
            lines.append(f"... 외 {len(queue) - max_items}곡")
        return "\n".join(lines)

    async def _search_tracks_ytdlp(self, query: str, requester: discord.abc.User):
        search_result = await YoutubeService.search(query)
        if search_result is None:
            return [], None, None

        def to_app(item: YoutubeSearch) -> MusicApplication:
            return MusicApplication(
                youtube_search=item,
                user_id=requester.id,
                user_name=requester.name,
                user_icon=requester.display_avatar.url,
            )

        if isinstance(search_result, YoutubePlaylist):
            tracks = [to_app(x) for x in search_result.songs]
            return tracks, search_result.title, search_result.song_cnt

        return [to_app(search_result)], None, None

    async def _search_tracks_lavalink(
        self, query: str, requester: discord.abc.User, limit: Optional[int] = None
    ):
        backend = self.audio_service.backend
        if not hasattr(backend, "_node") or backend._node is None:
            log_event("lavalink search skipped: backend._node is None")
            return [], None, None

        from core.network.youtube.internal.youtube_utile import is_youtube_url

        lavalink_query = query if is_youtube_url(query) else f"ytsearch:{query}"
        log_event(f"lavalink search query={lavalink_query}")
        results = await backend._node.get_tracks(query=lavalink_query)
        if results is None:
            log_event("lavalink search result: None")
            return [], None, None

        playlist_title = None
        playlist_count = None
        if isinstance(results, list):
            tracks = results
            log_event(f"lavalink search result: list size={len(tracks)}")
        elif hasattr(results, "tracks"):
            tracks = list(results.tracks)
            log_event(f"lavalink search result: tracks size={len(tracks)}")
            playlist_info = getattr(results, "playlist_info", None)
            if playlist_info is not None:
                playlist_title = getattr(playlist_info, "name", None) or getattr(playlist_info, "title", None)
            playlist_title = playlist_title or getattr(results, "name", None)
            playlist_count = len(tracks)
        else:
            tracks = [results]
            log_event("lavalink search result: single track object")

        if not tracks:
            log_event("lavalink search result: empty tracks")
            return [], None, None
        if limit is not None and limit > 0:
            tracks = tracks[:limit]

        def to_youtube_search(track) -> YoutubeSearch:
            title = getattr(track, "title", "Unknown")
            uri = getattr(track, "uri", "") or ""
            identifier = getattr(track, "identifier", "") or ""
            duration = getattr(track, "length", 0) or 0
            author = getattr(track, "author", "") or ""
            thumbnail = getattr(track, "thumbnail", None)
            if not thumbnail and identifier:
                thumbnail = f"https://i.ytimg.com/vi/{identifier}/hqdefault.jpg"
            video_url = uri or (f"https://www.youtube.com/watch?v={identifier}" if identifier else "")
            return YoutubeSearch(
                audio_source=uri or video_url,
                title=title,
                thumbnail_url=thumbnail or "",
                duration=duration,
                duration_string="",
                video_id=identifier,
                video_url=video_url,
                channel_id="",
                channel_url="",
                channel_name=author,
            )

        tracks_app = [MusicApplication(
            youtube_search=to_youtube_search(t),
            user_id=requester.id,
            user_name=requester.name,
            user_icon=requester.display_avatar.url,
        ) for t in tracks]

        return tracks_app, playlist_title, playlist_count

    async def _search_tracks_hybrid(
        self, query: str, requester: discord.abc.User, limit: Optional[int] = None
    ):
        from core.network.youtube.internal.youtube_utile import is_youtube_url

        if is_youtube_url(query):
            return await self._search_tracks_ytdlp(query, requester)

        tracks, _, _ = await self._search_tracks_lavalink(query, requester, limit=1)
        if not tracks:
            return [], None, None

        first = tracks[0].youtube_search
        video_url = first.video_url or first.audio_source
        if not video_url:
            return [], None, None

        resolved = await YoutubeService.url_search(video_url)
        if resolved is None:
            return [], None, None

        app = MusicApplication(
            youtube_search=resolved,
            user_id=requester.id,
            user_name=requester.name,
            user_icon=requester.display_avatar.url,
        )
        return [app], None, None

    async def _search_tracks(self, query: str, requester: discord.abc.User, limit: Optional[int] = None):
        from core.config import AUDIO_BACKEND

        if AUDIO_BACKEND == "lavalink":
            return await self._search_tracks_lavalink(query, requester, limit)
        if AUDIO_BACKEND == "hybrid":
            return await self._search_tracks_hybrid(query, requester, limit)
        return await self._search_tracks_ytdlp(query, requester)

    async def refresh_now_playing_embed(self, guild_id: int, *, is_paused: bool = False):
        status = await self.audio_service.get_status(guild_id)
        if status is None or status.now_playing is None:
            return
        music = status.now_playing
        if is_paused:
            embed = music_pause_embed(
                user_name=music.user_name,
                user_icon=music.user_icon,
                music_title=music.youtube_search.title,
                music_thumbnail=music.youtube_search.thumbnail_url,
                music_url=music.youtube_search.video_url,
                isLoop=status.loop,
            )
        else:
            embed = music_play_embed(
                user_name=music.user_name,
                user_icon=music.user_icon,
                music_title=music.youtube_search.title,
                music_thumbnail=music.youtube_search.thumbnail_url,
                music_url=music.youtube_search.video_url,
                isLoop=status.loop,
            )
        queue_preview = self.build_queue_preview(status.queue)
        if queue_preview:
            embed.add_field(name="대기열", value=queue_preview, inline=False)
        await self.music_message_edit(
            guild_id=guild_id,
            embed=embed,
            view=get_music_view(is_paused=is_paused, loop_enabled=status.loop)
        )

    async def _on_track_start(self, guild_id: int) -> None:
        await self.refresh_now_playing_embed(guild_id=guild_id, is_paused=False)

    async def _on_queue_empty(self, guild_id: int) -> None:
        await self.music_message_edit(
            guild_id=guild_id,
            embed=music_stop_embed(),
            view=None,
        )

    async def load_local_guild_channel(self):
        local_default_channels = await MusicDataSource.get_all()
        for i in local_default_channels:
            self.guild_channel[i.guild_id] = i
        log_event("로컬에서 Music 채널 불러옴.")
        log_event(f"guild_channel={self.guild_channel}")

    def guild_channel_ids(self) -> List[int]:
        return [model.channel_id for model in self.guild_channel.values()]

    def _get_action_state(self, guild_id: int) -> Dict[str, object]:
        state = self.guild_action_state.get(guild_id)
        if state is None:
            state = {"lock": asyncio.Lock(), "pending": 0}
            self.guild_action_state[guild_id] = state
        return state

    async def _send_action_message(
        self,
        ctx: commands.Context | Interaction,
        content: str,
        *,
        delete_after: float = 5,
        ephemeral: bool = False,
    ):
        if isinstance(ctx, Interaction):
            if ctx.response.is_done():
                message = await ctx.followup.send(content, ephemeral=ephemeral)
                if delete_after and not ephemeral:
                    async def delete_later():
                        try:
                            await asyncio.sleep(delete_after)
                            await message.delete()
                        except Exception:
                            pass
                    asyncio.create_task(delete_later())
            else:
                await ctx.response.send_message(content, delete_after=delete_after, ephemeral=ephemeral)
        else:
            await ctx.send(content, delete_after=delete_after)

    async def _run_serialized_action(
        self,
        ctx: commands.Context | Interaction,
        action_coro,
        *,
        success_message: str,
    ):
        state = self._get_action_state(ctx.guild.id)
        pending = state["pending"]
        if isinstance(pending, int) and pending >= MAX_GUILD_ACTION_PENDING:
            await self._send_action_message(
                ctx,
                "요청이 너무 많아 잠시 후 다시 시도해 주세요.",
                ephemeral=isinstance(ctx, Interaction),
            )
            return

        if isinstance(ctx, Interaction) and not ctx.response.is_done() and pending:
            await ctx.response.defer()

        state["pending"] = pending + 1 if isinstance(pending, int) else 1
        try:
            lock = state["lock"]
            if not isinstance(lock, asyncio.Lock):
                lock = asyncio.Lock()
                state["lock"] = lock
            async with lock:
                await action_coro()
            await self._send_action_message(ctx, success_message)
        except CommandError as exc:
            await self._send_action_message(
                ctx,
                exc.args[0],
                ephemeral=isinstance(ctx, Interaction),
            )
        finally:
            state["pending"] = max((state["pending"] or 1) - 1, 0)

    async def ensure_voice_model(self, message: discord.Message):
        return await self.audio_service.ensure_state(
            message.guild.id,
            message.author.voice.channel,
        )

    async def get_channel_message(self, guild_id: int) -> Optional[discord.Message]:
        music_model = self.guild_channel.get(guild_id, None)
        if music_model is None:
            return None
        
        retry_delays = [1, 2, 3]
        last_error = None
        for attempt, delay in enumerate(retry_delays, start=1):
            try:
                channel = await self.bot.fetch_channel(music_model.channel_id)
                return await channel.fetch_message(music_model.message_id)
            except discord.NotFound:
                raise
            except discord.Forbidden:
                raise
            except discord.HTTPException as exc:
                last_error = exc
                if getattr(exc, "status", None) is not None and exc.status < 500:
                    raise
                log_event(f"get_channel_message retry {attempt}/3 failed: {exc}")
            except (discord.DiscordServerError, asyncio.TimeoutError, OSError, ConnectionResetError) as exc:
                last_error = exc
                log_event(f"get_channel_message retry {attempt}/3 failed: {exc}")
            if attempt < len(retry_delays):
                await asyncio.sleep(delay)

        log_event(f"get_channel_message failed after retries: {last_error}")
        return None


    def check_voice_play(self, ctx: commands.Context | Interaction):
        """
        해당 함수는 보이스의 설정과 유저의 보이스 입장을 선별하는 데코레이터 함수입니다.
        :raise CommandError:
        :return:
        """
        if not self.audio_service.has_state(ctx.guild.id):
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
                state = self.audio_service.states.get(member.guild.id)
                if state is not None:
                    state.voice_channel_id = after.channel.id
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
        await self.audio_service.play_next(guild_id, beforeMusic)

    async def clear_guild_queue(self, guild_id: int):
        await self.audio_service.stop(guild_id)
        await self.audio_service.disconnect(guild_id)
        await self.music_message_edit(
            guild_id=guild_id,
            embed=music_stop_embed(),
            view=None,
        )

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
            if message is None:
                log_event("music_message_edit aborted: channel/message fetch failed")
                return
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
        status = await self.audio_service.get_status(ctx.guild.id)
        if status is None or status.now_playing is None:
            raise CommandError("??? ??????????? ??? ?????")

        await self.audio_service.pause(ctx.guild.id)
        await self.refresh_now_playing_embed(ctx.guild.id, is_paused=True)

    async def _resume(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)
        await self.audio_service.resume(ctx.guild.id)
        await self.refresh_now_playing_embed(ctx.guild.id, is_paused=False)

    async def _stop(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)
        await self.clear_guild_queue(ctx.guild.id)

    async def _skip(self, ctx: commands.Context | Interaction):
        self.check_voice_play(ctx)
        await self.audio_service.skip(ctx.guild.id)

    async def _loop(self, ctx: commands.Context | Interaction) -> str:
        self.check_voice_play(ctx)
        loop_enabled = await self.audio_service.toggle_loop(ctx.guild.id)
        status = await self.audio_service.get_status(ctx.guild.id)
        is_paused = status.is_paused if status else False
        await self.refresh_now_playing_embed(
            ctx.guild.id,
            is_paused=is_paused
        )
        state = "???" if loop_enabled else "???"
        return f"?????{state}??? ????????"

    async def _shuffle(self, ctx: commands.Context | Interaction) -> str:
        self.check_voice_play(ctx)
        status = await self.audio_service.get_status(ctx.guild.id)
        if status is None or len(status.queue) < 2:
            raise CommandError("???????? ???????")
        await self.audio_service.shuffle(ctx.guild.id)
        await self.refresh_now_playing_embed(
            ctx.guild.id,
            is_paused=status.is_paused
        )
        return "?????????????."

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
            if (interaction.client.user.id != self.bot.user.id 
                or interaction.type != discord.InteractionType.component 
                or interaction.data.get("custom_id", None) is None):
                return
            log_event(interaction.message)

            custom_id = interaction.data["custom_id"]
            if custom_id == "stop":
                return await self._run_serialized_action(
                    interaction,
                    lambda: self._stop(interaction),
                    success_message="음악 재생을 초기화했어요",
                )
            elif custom_id == "pause":
                await self._pause(interaction)
                return await self._send_action_message(
                    interaction,
                    "음악 재생을 일시정지 했어요",
                )
            elif custom_id == "resume":
                await self._resume(interaction)
                return await self._send_action_message(
                    interaction,
                    "음악 재생을 재개 했어요",
                )
            elif custom_id == "skip":
                return await self._run_serialized_action(
                    interaction,
                    lambda: self._skip(interaction),
                    success_message="음악 재생을 넘겼어요",
                )
            elif custom_id == "loop":
                message = await self._loop(interaction)
                return await self._send_action_message(interaction, message)
            elif custom_id == "shuffle":
                message = await self._shuffle(interaction)
                return await self._send_action_message(interaction, message)



            await self._send_action_message(
                interaction,
                f"{interaction.data['custom_id']} 버튼이 클릭되었습니다!",
            )
        except Exception as exception:
            if isinstance(exception, CommandError):
                return await self._send_action_message(
                    interaction,
                    f"{exception.args[0]}",
                    ephemeral=True,
                )
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
        await self._run_serialized_action(
            ctx,
            lambda: self._stop(ctx),
            success_message="음악 재생을 초기화했어요",
        )

    @commands.command("스킵")
    async def skip(self, ctx: commands.Context):
        await self._run_serialized_action(
            ctx,
            lambda: self._skip(ctx),
            success_message="음악 재생을 넘겼어요",
        )

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

        existing_state = self.audio_service.states.get(message.guild.id)
        if existing_state is not None and message.author.voice.channel.id != existing_state.voice_channel_id:
            return

        await self.ensure_voice_model(message)

        async def delete_message():
            try:
                await message.delete(delay=5)
            except:
                pass

        await delete_message()

        tracks, playlist_title, playlist_count = await self._search_tracks(message.content, message.author, limit=1)

        async def send_and_delete_message(content: str):
            await message.channel.send(content, delete_after=5)

        if not tracks:
            await send_and_delete_message("노래를 찾지 못했어요..")
            return

        await self.audio_service.enqueue_and_play(message.guild.id, message.author.voice.channel, tracks)

        if playlist_title:
            count_text = playlist_count if playlist_count is not None else len(tracks)
            await send_and_delete_message(f"{playlist_title} 플레이 리스트를 추가했어요!\n추가된 곡 수: {count_text}")
        else:
            await send_and_delete_message(f"{tracks[0].youtube_search.title} 곡을 추가했어요!")

        status = await self.audio_service.get_status(message.guild.id)
        if status:
            await self.refresh_now_playing_embed(
                message.guild.id,
                is_paused=status.is_paused
            )












async def setup(bot):
    await bot.add_cog(Music(bot))
