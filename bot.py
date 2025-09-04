#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import collections
import io
import logging
import time
import wave
from typing import Dict

import discord
from discord.ext import tasks
import speech_recognition as sr

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LeoScribeBot")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def utcnow():
    # Pycord exposes discord.utils.utcnow()
    return discord.utils.utcnow()


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


# ─────────────────────────────────────────────────────────────────────────────
# Voice Sink (Pycord)
# ─────────────────────────────────────────────────────────────────────────────
class TranscriptionSink(discord.sinks.WaveSink):
    def __init__(self, bot: "LeoScribeBot", channel: discord.abc.Messageable):
        super().__init__()
        self.bot = bot
        self.channel = channel
        self.user_buffers: Dict[int, UserAudioBuffer] = {}
        self.recognizer = sr.Recognizer()
        self.processing = True

    def write(self, data, user):
        if not self.processing:
            return
        if user.id not in self.user_buffers:
            self.user_buffers[user.id] = UserAudioBuffer(user.id)
        # Raw PCM from Pycord (48kHz 16-bit stereo)
        self.user_buffers[user.id].add_audio(data.raw_data)

    async def transcribe_and_send(self, user_id: int, audio_data: bytes):
        if not audio_data:
            return
        try:
            # Wrap raw PCM -> WAV container (48kHz, 16-bit, stereo)
            audio_io = io.BytesIO()
            with wave.open(audio_io, "wb") as wav_file:
                wav_file.setnchannels(2)
                wav_file.setsampwidth(2)
                wav_file.setframerate(48000)
                wav_file.writeframes(audio_data)
            audio_io.seek(0)

            # SpeechRecognition (Google)
            with sr.AudioFile(audio_io) as source:
                audio = self.recognizer.record(source)
            text = self.recognizer.recognize_google(audio)

            if text.strip():
                user = self.bot.get_user(user_id)
                username = user.display_name if user else f"User {user_id}"
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


# Finish callback for Pycord's start_recording
def _on_finish(_sink: TranscriptionSink, *_args, **_kwargs):
    # We stream & transcribe on-the-fly; nothing else needed on finish.
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Control Panel View
# ─────────────────────────────────────────────────────────────────────────────
class TranscriptionView(discord.ui.View):
    """Interactive button panel for transcription control"""

    def __init__(self, bot: "LeoScribeBot", guild_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.bot = bot
        self.guild_id = guild_id
        self.update_buttons()

    def update_buttons(self):
        is_active = self.guild_id in self.bot.active_sessions

        # Rebuild buttons
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
        gid = interaction.guild.id if interaction.guild else self.guild_id

        if not interaction.user.voice:
            await interaction.response.send_message(
                "❌ You need to be in a voice channel to start transcription!",
                ephemeral=True,
            )
            return

        # Ensure /setup has been run
        if gid not in self.bot.transcription_channels:
            await interaction.response.send_message(
                "⚠️ Please run `/setup` first so I know where to post transcripts.",
                ephemeral=True,
            )
            return

        voice_channel = interaction.user.voice.channel

        try:
            # Get transcription channel
            channel_id = self.bot.transcription_channels[gid]
            transcript_channel = self.bot.get_channel(channel_id)
            if transcript_channel is None:
                await interaction.response.send_message(
                    "❌ I can't access the configured transcription channel. Check my permissions.",
                    ephemeral=True,
                )
                return

            # Connect to voice channel (reuse existing if already connected)
            voice_client = interaction.guild.voice_client
            if voice_client and voice_client.channel != voice_channel:
                await voice_client.disconnect(force=True)
                voice_client = None
            if voice_client is None:
                voice_client = await voice_channel.connect()

            # Create transcription sink
            sink = TranscriptionSink(self.bot, transcript_channel)
            self.bot.active_sessions[gid] = sink

            # Start recording (provide a finish callback)
            voice_client.start_recording(sink, _on_finish)

            # Replace panel with a fresh view reflecting new state
            new_view = TranscriptionView(self.bot, gid)
            await interaction.response.edit_message(
                embed=new_view.get_status_embed("🔴 Recording Active", voice_channel.name),
                view=new_view,
            )

            # Notify in transcript channel
            notify = discord.Embed(
                description=f"🔴 **Recording started** in {voice_channel.mention}",
                color=0xFF0000,
                timestamp=utcnow(),
            )
            await transcript_channel.send(embed=notify)

        except Exception as e:
            logger.error(f"Error starting transcription: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while starting transcription. Check my permissions.",
                    ephemeral=True,
                )

    async def stop_callback(self, interaction: discord.Interaction):
        """Handle stop button click"""
        gid = interaction.guild.id if interaction.guild else self.guild_id
        try:
            voice_client = interaction.guild.voice_client
            if voice_client:
                voice_client.stop_recording()
                await voice_client.disconnect()

            # Cleanup session
            if gid in self.bot.active_sessions:
                sink: TranscriptionSink = self.bot.active_sessions[gid]
                sink.cleanup()
                del self.bot.active_sessions[gid]

            # Replace panel with a fresh view reflecting idle state
            new_view = TranscriptionView(self.bot, gid)
            await interaction.response.edit_message(
                embed=new_view.get_status_embed("⏹️ Recording Stopped", "Ready for next session"),
                view=new_view,
            )

            # Notify in transcript channel
            channel_id = self.bot.transcription_channels.get(gid)
            transcript_channel = self.bot.get_channel(channel_id) if channel_id else None
            if transcript_channel:
                notify = discord.Embed(
                    description="⏹️ **Recording stopped**\n💡 *Tip: Click 'Clear Chat' to prepare for your next session*",
                    color=0x808080,
                    timestamp=utcnow(),
                )
                await transcript_channel.send(embed=notify)

        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while stopping transcription.",
                    ephemeral=True,
                )

    async def clear_callback(self, interaction: discord.Interaction):
        """Handle clear button click"""
        gid = interaction.guild.id if interaction.guild else self.guild_id

        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ You need 'Manage Messages' permission to clear the chat.",
                ephemeral=True,
            )
            return

        try:
            channel_id = self.bot.transcription_channels.get(gid)
            transcript_channel = self.bot.get_channel(channel_id) if channel_id else None
            if transcript_channel is None:
                await interaction.response.send_message(
                    "⚠️ No transcription channel configured. Run `/setup` first.",
                    ephemeral=True,
                )
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

            await interaction.response.send_message(
                f"✅ Cleared {deleted_count} messages from the transcription channel!",
                ephemeral=True,
            )

            welcome = discord.Embed(
                title="🎤 LeoScribeBot Transcription Channel",
                description="Ready for voice chat transcription!\n\nUse the control panel above to start recording.",
                color=0x00FF00,
                timestamp=utcnow(),
            )
            await transcript_channel.send(embed=welcome)

        except Exception as e:
            logger.error(f"Error clearing messages: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while clearing messages.",
                    ephemeral=True,
                )

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


# ─────────────────────────────────────────────────────────────────────────────
# Bot
# ─────────────────────────────────────────────────────────────────────────────
class LeoScribeBot(discord.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True

        # In Pycord, prefix is optional; we use slash commands.
        super().__init__(intents=intents)

        self.transcription_channels: Dict[int, int] = {}         # guild_id -> channel_id
        self.active_sessions: Dict[int, TranscriptionSink] = {}  # guild_id -> sink
        self.control_panels: Dict[int, int] = {}                 # guild_id -> message_id

    async def setup_hook(self):
        # Register a "template" persistent view so buttons keep working after restarts.
        # We rebuild a guild-specific view on each click anyway.
        self.add_view(TranscriptionView(self, 0))
        await self.sync_commands()
        logger.info("Slash commands synced!")

    async def on_ready(self):
        logger.info(f"{self.user} has connected to Discord!")
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


# ─────────────────────────────────────────────────────────────────────────────
# Slash commands (Pycord)
# ─────────────────────────────────────────────────────────────────────────────
bot = LeoScribeBot()


@bot.slash_command(name="setup", description="Create a dedicated channel with interactive controls")
async def setup_command(ctx: discord.ApplicationContext):
    """Create a dedicated transcription channel with control panel."""
    if not ctx.user.guild_permissions.manage_channels:
        await ctx.respond(
            "❌ You need 'Manage Channels' permission to use this command.",
            ephemeral=True,
        )
        return

    guild = ctx.guild
    channel_name = "leo-scribebot"

    # Check if channel already exists
    existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
    if existing_channel:
        bot.transcription_channels[guild.id] = existing_channel.id

        # Create new control panel
        view = TranscriptionView(bot, guild.id)
        embed = view.get_status_embed("⚪ Ready", "Waiting to start")

        control_message = await existing_channel.send(embed=embed, view=view)
        bot.control_panels[guild.id] = control_message.id

        await ctx.respond(
            f"✅ Using existing channel {existing_channel.mention} with fresh control panel!",
            ephemeral=True,
        )
        return

    try:
        # Create the channel
        channel = await guild.create_text_channel(
            channel_name,
            topic="Real-time voice chat transcriptions by LeoScribeBot 🎤📝",
            reason="LeoScribeBot transcription channel",
        )
        bot.transcription_channels[guild.id] = channel.id

        # Control panel
        view = TranscriptionView(bot, guild.id)
        embed = view.get_status_embed("⚪ Ready", "Channel created successfully")
        control_message = await channel.send(embed=embed, view=view)
        bot.control_panels[guild.id] = control_message.id

        # Welcome message
        welcome = discord.Embed(
            title="🎤 LeoScribeBot Transcription Channel",
            description="Welcome to your voice transcription channel!\n\nUse the control panel above to manage recording sessions.",
            color=0x00FF00,
        )
        await channel.send(embed=welcome)

        await ctx.respond(
            f"✅ Created transcription channel {channel.mention} with interactive controls!",
            ephemeral=True,
        )

    except discord.Forbidden:
        await ctx.respond(
            "❌ I don't have permission to create channels. Please check my permissions.",
            ephemeral=True,
        )
    except Exception as e:
        logger.error(f"Error creating channel: {e}")
        await ctx.respond(
            "❌ An error occurred while creating the channel.",
            ephemeral=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    bot.run(os.getenv("DISCORD_TOKEN"))
