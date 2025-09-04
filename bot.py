#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import io
import time
import wave
import asyncio
import logging
import collections
from pathlib import Path
from typing import Dict

import discord
from discord.ext import tasks
import speech_recognition as sr

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LeoScribeBot")
# For deeper debugging:
# logging.getLogger("discord").setLevel(logging.DEBUG)

# Local modules
from storage import GuildStore
from voice_utils import ensure_opus_loaded, connect_voice_fresh, VoiceConnectError

def utcnow():
    return discord.utils.utcnow()

async def _safe_defer(interaction: discord.Interaction):
    """Ack the interaction quickly to avoid 10062 Unknown interaction."""
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except Exception:
            pass

# ─────────────────────────────
# Audio buffering per user
# ─────────────────────────────
class UserAudioBuffer:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.audio_frames = collections.deque()
        self.last_received = time.time()

    def add_audio(self, audio_data: bytes):
        self.audio_frames.append(audio_data)
        self.last_received = time.time()

    def get_audio_data(self) -> bytes:
        if not self.audio_frames:
            return b""
        return b"".join(self.audio_frames)

    def clear(self):
        self.audio_frames.clear()

    def is_silent(self, threshold_seconds: float = 1.5) -> bool:
        return time.time() - self.last_received > threshold_seconds

# ─────────────────────────────
# Pycord WaveSink for recording
# Pycord calls: sink.write(pcm_bytes: bytes, user_id: int)
# ─────────────────────────────
class TranscriptionSink(discord.sinks.WaveSink):
    def __init__(self, bot: "LeoScribeBot", channel: discord.abc.Messageable):
        super().__init__()
        self.bot = bot
        self.channel = channel
        self.user_buffers: Dict[int, UserAudioBuffer] = {}
        self.recognizer = sr.Recognizer()
        self.processing = True

    def write(self, pcm_bytes: bytes, user_id: int):
        if not self.processing:
            return
        if user_id not in self.user_buffers:
            self.user_buffers[user_id] = UserAudioBuffer(user_id)
        self.user_buffers[user_id].add_audio(pcm_bytes)

    async def transcribe_and_send(self, user_id: int, audio_data: bytes):
        if not audio_data:
            return
        try:
            # Wrap PCM in WAV for SpeechRecognition
            audio_io = io.BytesIO()
            with wave.open(audio_io, "wb") as wav_file:
                wav_file.setnchannels(2)      # stereo
                wav_file.setsampwidth(2)      # 16-bit
                wav_file.setframerate(48000)  # Discord sample rate
                wav_file.writeframes(audio_data)
            audio_io.seek(0)

            with sr.AudioFile(audio_io) as source:
                audio = self.recognizer.record(source)
            text = self.recognizer.recognize_google(audio)

            if text.strip():
                # Resolve username best-effort
                username = f"User {user_id}"
                try:
                    guild = getattr(self.channel, "guild", None)
                    user_obj = None
                    if guild:
                        user_obj = guild.get_member(user_id)
                    if not user_obj:
                        user_obj = self.bot.get_user(user_id)
                    if not user_obj:
                        user_obj = await self.bot.fetch_user(user_id)
                    if user_obj:
                        username = getattr(user_obj, "display_name", None) or getattr(user_obj, "name", username)
                except Exception:
                    pass

                embed = discord.Embed(
                    description=f"**{username}:** {text}",
                    color=0x3498DB,
                    timestamp=utcnow(),
                )
                await self.channel.send(embed=embed)

        except sr.UnknownValueError:
            logger.debug(f"Could not understand audio from user {user_id}")
        except sr.RequestError as e:
            logger.error(f"Speech recognition error: {e}")
        except Exception as e:
            logger.error(f"Transcription error: {e}")

    def cleanup(self):
        self.processing = False

# ─────────────────────────────
# Control Panel View
# ─────────────────────────────
class TranscriptionView(discord.ui.View):
    """Interactive button panel for transcription control"""

    def __init__(self, bot: "LeoScribeBot", guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.bot = bot
        self.guild_id = guild_id
        self.update_buttons()

    def update_buttons(self):
        is_active = self.guild_id in self.bot.active_sessions

        self.clear_items()

        start_button = discord.ui.Button(
            label="🎙️ Start Recording",
            style=discord.ButtonStyle.green if not is_active else discord.ButtonStyle.gray,
            disabled=is_active,
            custom_id="start_transcription",
        )
        start_button.callback = self.start_callback
        self.add_item(start_button)

        stop_button = discord.ui.Button(
            label="⏹️ Stop Recording",
            style=discord.ButtonStyle.red if is_active else discord.ButtonStyle.gray,
            disabled=not is_active,
            custom_id="stop_transcription",
        )
        stop_button.callback = self.stop_callback
        self.add_item(stop_button)

        clear_button = discord.ui.Button(
            label="🗑️ Clear Chat",
            style=discord.ButtonStyle.secondary,
            custom_id="clear_transcription",
        )
        clear_button.callback = self.clear_callback
        self.add_item(clear_button)

    async def start_callback(self, interaction: discord.Interaction):
        """Handle start button click"""
        await _safe_defer(interaction)
        gid = interaction.guild.id if interaction.guild else self.guild_id

        # Prevent double-starts
        if gid in self.bot.active_sessions:
            try:
                await interaction.followup.send("⚠️ Already recording in this server. Click **Stop** first.", ephemeral=True)
            except Exception:
                pass
            return

        if not interaction.user.voice:
            try:
                await interaction.followup.send("❌ You need to be in a voice channel to start transcription!", ephemeral=True)
            except Exception:
                pass
            return

        if gid not in self.bot.transcription_channels:
            try:
                await interaction.followup.send("⚠️ Please run `/setup` first so I know where to post transcripts.", ephemeral=True)
            except Exception:
                pass
            return

        voice_channel = interaction.user.voice.channel
        channel_id = self.bot.transcription_channels[gid]
        transcript_channel = self.bot.get_channel(channel_id)

        if transcript_channel is None:
            try:
                await interaction.followup.send("❌ I can't access the configured transcription channel. Check my permissions.", ephemeral=True)
            except Exception:
                pass
            return

        # Optional guard: if a VoiceClient is already attached, try to reuse/move it
        existing_vc = interaction.guild.voice_client if interaction.guild else None
        if existing_vc and existing_vc.is_connected():
            if existing_vc.channel.id != voice_channel.id:
                try:
                    await existing_vc.move_to(voice_channel)
                except Exception:
                    # If move fails, connect_voice_fresh will handle reset/reconnect
                    pass

        # ---- Voice connect with 4006 retry ----
        try:
            voice_client = await connect_voice_fresh(interaction.guild, voice_channel)
            await asyncio.sleep(0.5)

        except discord.errors.ConnectionClosed as e:
            # Some guilds/channels throw a first-time 4006 invalid session.
            if getattr(e, "code", None) == 4006:
                logger.warning("Voice WS 4006 on first attempt; hard-resetting and retrying once…")
                await asyncio.sleep(1.5)  # let Discord drop the bad session
                voice_client = await connect_voice_fresh(interaction.guild, voice_channel)
                await asyncio.sleep(0.5)
            else:
                logger.error(f"Unexpected ConnectionClosed during start: {e}")
                new_view = TranscriptionView(self.bot, gid)
                await interaction.message.edit(
                    embed=new_view.get_status_embed("❌ Error", "Voice gateway closed unexpectedly."),
                    view=new_view,
                )
                try:
                    await interaction.followup.send("❌ Voice gateway closed unexpectedly.", ephemeral=True)
                except Exception:
                    pass
                return

        except VoiceConnectError as e:
            logger.error(f"Voice connection failed: {e}")
            new_view = TranscriptionView(self.bot, gid)
            await interaction.message.edit(
                embed=new_view.get_status_embed("❌ Error", str(e)),
                view=new_view,
            )
            try:
                await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            except Exception:
                pass
            return

        except Exception as e:
            logger.error(f"Unexpected error starting recording: {e}")
            new_view = TranscriptionView(self.bot, gid)
            await interaction.message.edit(
                embed=new_view.get_status_embed("❌ Error", "An unexpected error occurred"),
                view=new_view,
            )
            try:
                await interaction.followup.send("❌ Unexpected error while starting.", ephemeral=True)
            except Exception:
                pass
            return

        # ---- Start recording if we got here ----
        try:
            sink = TranscriptionSink(self.bot, transcript_channel)
            self.bot.active_sessions[gid] = sink

            # ASYNC finish callback required by py-cord
            async def _on_finish(_sink_obj, *args, **kwargs):
                logger.info("Recording finished")
                try:
                    if self.bot.active_sessions.get(gid) is _sink_obj:
                        _sink_obj.cleanup()
                        self.bot.active_sessions.pop(gid, None)
                        new_view = TranscriptionView(self.bot, gid)
                        try:
                            await interaction.message.edit(
                                embed=new_view.get_status_embed("⏹️ Recording Stopped", "Session ended"),
                                view=new_view,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

            voice_client.start_recording(sink, _on_finish)

            new_view = TranscriptionView(self.bot, gid)
            await interaction.message.edit(
                embed=new_view.get_status_embed("🔴 Recording Active", voice_channel.name),
                view=new_view,
            )

            await transcript_channel.send(
                embed=discord.Embed(
                    description=f"🔴 **Recording started** in {voice_channel.mention}",
                    color=0xFF0000,
                    timestamp=utcnow(),
                )
            )

        except Exception as e:
            logger.error(f"Failed to start recording after connect: {e}")
            new_view = TranscriptionView(self.bot, gid)
            await interaction.message.edit(
                embed=new_view.get_status_embed("❌ Error", "Failed to start recording."),
                view=new_view,
            )
            try:
                await interaction.followup.send("❌ Failed to start recording.", ephemeral=True)
            except Exception:
                pass

    async def stop_callback(self, interaction: discord.Interaction):
        """Handle stop button click"""
        await _safe_defer(interaction)
        gid = interaction.guild.id if interaction.guild else self.guild_id

        try:
            voice_client = interaction.guild.voice_client if interaction.guild else None
            if voice_client:
                try:
                    voice_client.stop_recording()
                except Exception:
                    pass
                try:
                    await voice_client.disconnect()
                except Exception:
                    pass

            if gid in self.bot.active_sessions:
                sink: TranscriptionSink = self.bot.active_sessions[gid]
                sink.cleanup()
                self.bot.active_sessions.pop(gid, None)

            new_view = TranscriptionView(self.bot, gid)
            await interaction.message.edit(
                embed=new_view.get_status_embed("⏹️ Recording Stopped", "Ready for next session"),
                view=new_view,
            )

            channel_id = self.bot.transcription_channels.get(gid)
            transcript_channel = self.bot.get_channel(channel_id) if channel_id else None
            if transcript_channel:
                await transcript_channel.send(
                    embed=discord.Embed(
                        description="⏹️ **Recording stopped**\n💡 *Tip: Click 'Clear Chat' to prepare for your next session*",
                        color=0x808080,
                        timestamp=utcnow(),
                    )
                )

        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")
            try:
                await interaction.followup.send("❌ An error occurred while stopping transcription.", ephemeral=True)
            except Exception:
                pass

    async def clear_callback(self, interaction: discord.Interaction):
        """Handle clear button click"""
        await _safe_defer(interaction)
        gid = interaction.guild.id if interaction.guild else self.guild_id

        if not interaction.user.guild_permissions.manage_messages:
            try:
                await interaction.followup.send("❌ You need 'Manage Messages' permission to clear the chat.", ephemeral=True)
            except Exception:
                pass
            return

        try:
            channel_id = self.bot.transcription_channels.get(gid)
            transcript_channel = self.bot.get_channel(channel_id) if channel_id else None
            if transcript_channel is None:
                try:
                    await interaction.followup.send("⚠️ No transcription channel configured. Run `/setup` first.", ephemeral=True)
                except Exception:
                    pass
                return

            deleted_count = 0
            async for message in transcript_channel.history(limit=None):
                if message.embeds and message.embeds[0].title == "🎤 LeoScribeBot Control Panel":
                    continue
                try:
                    await message.delete()
                    deleted_count += 1
                except Exception:
                    pass

            try:
                await interaction.followup.send(f"✅ Cleared {deleted_count} messages from the transcription channel!", ephemeral=True)
            except Exception:
                pass

            welcome = discord.Embed(
                title="🎤 LeoScribeBot Transcription Channel",
                description="Ready for voice chat transcription!\n\nUse the control panel above to start recording.",
                color=0x00FF00,
                timestamp=utcnow(),
            )
            await transcript_channel.send(embed=welcome)

        except Exception as e:
            logger.error(f"Error clearing messages: {e}")
            try:
                await interaction.followup.send("❌ An error occurred while clearing messages.", ephemeral=True)
            except Exception:
                pass

    def get_status_embed(self, status: str, details: str = "") -> discord.Embed:
        embed = discord.Embed(
            title="🎤 LeoScribeBot Control Panel",
            color=0x3498DB,
            timestamp=utcnow(),
        )
        embed.add_field(name="Status", value=status, inline=True)
        if details:
            embed.add_field(name="Details", value=details, inline=True)
        embed.add_field(
            name="Instructions",
            value="• Join a voice channel\n• Click Start to begin\n• Click Stop when done\n• Click Clear to reset",
            inline=False,
        )
        return embed

# ─────────────────────────────
# Bot
# ─────────────────────────────
class LeoScribeBot(discord.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)

        # Runtime state
        self.transcription_channels: Dict[int, int] = {}         # guild_id -> channel_id
        self.active_sessions: Dict[int, TranscriptionSink] = {}  # guild_id -> sink
        self.control_panels: Dict[int, int] = {}                 # guild_id -> message_id

        # Persisted config
        data_dir = Path(__file__).parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.store = GuildStore(data_dir / "guild_store.json")

        # Load saved channels
        for gid_str, ch_id in self.store.get_channels().items():
            self.transcription_channels[int(gid_str)] = ch_id

        # Voice deps
        ensure_opus_loaded()

    async def setup_hook(self):
        # Register a generic persistent view so custom_id callbacks work after restart
        self.add_view(TranscriptionView(self, 0))
        await self.sync_commands()
        logger.info("Slash commands synced!")

    async def on_ready(self):
        logger.info(f"{self.user} has connected to Discord!")

        # Optionally refresh panels in saved guilds
        for gid, ch_id in self.transcription_channels.items():
            ch = self.get_channel(ch_id)
            if ch:
                view = TranscriptionView(self, gid)
                try:
                    await ch.send(
                        embed=view.get_status_embed("⚪ Ready", "Restored after restart"),
                        view=view,
                    )
                except discord.Forbidden:
                    logger.warning(f"No permission to send to channel {ch_id} in guild {gid}")
                except Exception as e:
                    logger.error(f"Error sending startup message: {e}")

        if not self.check_for_silence.is_running():
            self.check_for_silence.start()

    @tasks.loop(seconds=1)
    async def check_for_silence(self):
        """When a user stops talking for a bit, transcribe their buffered audio."""
        for _gid, sink in list(self.active_sessions.items()):
            if not sink.processing:
                continue
            for user_id, buffer in list(sink.user_buffers.items()):
                if buffer.is_silent() and buffer.audio_frames:
                    audio_data = buffer.get_audio_data()
                    buffer.clear()
                    asyncio.create_task(sink.transcribe_and_send(user_id, audio_data))

    @check_for_silence.before_loop
    async def before_check_for_silence(self):
        await self.wait_until_ready()

    async def on_guild_remove(self, guild: discord.Guild):
        """Clean up persisted data when the bot leaves a guild."""
        self.store.remove_guild(guild.id)
        self.transcription_channels.pop(guild.id, None)
        self.active_sessions.pop(guild.id, None)
        self.control_panels.pop(guild.id, None)

# ─────────────────────────────
# Slash Commands
# ─────────────────────────────
bot = LeoScribeBot()

@bot.slash_command(name="setup", description="Create a dedicated channel with interactive controls")
async def setup_command(ctx: discord.ApplicationContext):
    """Create (or reuse) a transcription channel and post the control panel."""
    if not ctx.user.guild_permissions.manage_channels:
        await ctx.respond("❌ You need 'Manage Channels' permission to use this command.", ephemeral=True)
        return

    guild = ctx.guild
    channel_name = "leo-scribebot"

    existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
    if existing_channel:
        bot.transcription_channels[guild.id] = existing_channel.id
        bot.store.set_channel(guild.id, existing_channel.id)

        view = TranscriptionView(bot, guild.id)
        embed = view.get_status_embed("⚪ Ready", "Waiting to start")
        control_message = await existing_channel.send(embed=embed, view=view)

        bot.control_panels[guild.id] = control_message.id
        bot.store.set_panel(guild.id, control_message.id)

        await ctx.respond(f"✅ Using existing channel {existing_channel.mention} with fresh control panel!", ephemeral=True)
        return

    try:
        channel = await guild.create_text_channel(
            channel_name,
            topic="Real-time voice chat transcriptions by LeoScribeBot 🎤📝",
            reason="LeoScribeBot transcription channel",
        )
        bot.transcription_channels[guild.id] = channel.id
        bot.store.set_channel(guild.id, channel.id)

        view = TranscriptionView(bot, guild.id)
        embed = view.get_status_embed("⚪ Ready", "Channel created successfully")

        control_message = await channel.send(embed=embed, view=view)
        bot.control_panels[guild.id] = control_message.id
        bot.store.set_panel(guild.id, control_message.id)

        welcome = discord.Embed(
            title="🎤 LeoScribeBot Transcription Channel",
            description="Welcome to your voice transcription channel!\n\nUse the control panel above to manage recording sessions.",
            color=0x00FF00,
        )
        await channel.send(embed=welcome)

        await ctx.respond(f"✅ Created transcription channel {channel.mention} with interactive controls!", ephemeral=True)

    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to create channels. Please check my permissions.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error creating channel: {e}")
        await ctx.respond("❌ An error occurred while creating the channel.", ephemeral=True)

# Entrypoint
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN not set")
    bot.run(token)
