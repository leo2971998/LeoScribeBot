import discord
from discord.ext import commands, tasks
import asyncio
import speech_recognition as sr
import io
import wave
import collections
import time
from typing import Dict, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class UserAudioBuffer:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.audio_frames = collections.deque()
        self.last_received = time.time()
        
    def add_audio(self, audio_data):
        self.audio_frames.append(audio_data)
        self.last_received = time.time()
        
    def get_audio_data(self) -> bytes:
        """Combine all audio frames into a single audio buffer"""
        if not self.audio_frames:
            return b''
        return b''.join(self.audio_frames)
        
    def clear(self):
        self.audio_frames.clear()
        
    def is_silent(self, threshold_seconds: float = 1.5) -> bool:
        return time.time() - self.last_received > threshold_seconds

class TranscriptionSink(discord.sinks.WaveSink):
    def __init__(self, bot, channel):
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
            
        # Store raw audio data for this user
        self.user_buffers[user.id].add_audio(data.raw_data)

    async def transcribe_and_send(self, user_id: int, audio_data: bytes):
        """Transcribe audio data and send to channel"""
        if not audio_data:
            return
            
        try:
            # Convert raw audio to WAV format
            audio_io = io.BytesIO()
            with wave.open(audio_io, 'wb') as wav_file:
                wav_file.setnchannels(2)  # Stereo
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(48000)  # Discord's sample rate
                wav_file.writeframes(audio_data)
            
            audio_io.seek(0)
            
            # Use speech recognition
            with sr.AudioFile(audio_io) as source:
                audio = self.recognizer.record(source)
                
            text = self.recognizer.recognize_google(audio)
            
            if text.strip():
                # Get user info
                user = self.bot.get_user(user_id)
                username = user.display_name if user else f"User {user_id}"
                
                # Send transcript to channel
                embed = discord.Embed(
                    description=f"**{username}:** {text}",
                    color=0x3498db,
                    timestamp=discord.utils.utcnow()
                )
                await self.channel.send(embed=embed)
                
        except sr.UnknownValueError:
            # Speech was not clear enough
            logger.debug(f"Could not understand audio from user {user_id}")
        except sr.RequestError as e:
            logger.error(f"Speech recognition error: {e}")
        except Exception as e:
            logger.error(f"Transcription error: {e}")

    def cleanup(self):
        self.processing = False

class TranscriptionView(discord.ui.View):
    """Interactive button panel for transcription control"""
    
    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)  # Persistent view
        self.bot = bot
        self.guild_id = guild_id
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on current session status"""
        is_active = self.guild_id in self.bot.active_sessions
        
        # Clear all items and re-add with updated states
        self.clear_items()
        
        # Start button
        start_button = discord.ui.Button(
            label="🎙️ Start Recording",
            style=discord.ButtonStyle.green if not is_active else discord.ButtonStyle.gray,
            disabled=is_active,
            custom_id="start_transcription"
        )
        start_button.callback = self.start_callback
        self.add_item(start_button)
        
        # Stop button
        stop_button = discord.ui.Button(
            label="⏹️ Stop Recording",
            style=discord.ButtonStyle.red if is_active else discord.ButtonStyle.gray,
            disabled=not is_active,
            custom_id="stop_transcription"
        )
        stop_button.callback = self.stop_callback
        self.add_item(stop_button)
        
        # Clear button
        clear_button = discord.ui.Button(
            label="🗑️ Clear Chat",
            style=discord.ButtonStyle.secondary,
            custom_id="clear_transcription"
        )
        clear_button.callback = self.clear_callback
        self.add_item(clear_button)
    
    async def start_callback(self, interaction: discord.Interaction):
        """Handle start button click"""
        if not interaction.user.voice:
            await interaction.response.send_message(
                "❌ You need to be in a voice channel to start transcription!",
                ephemeral=True
            )
            return
        
        voice_channel = interaction.user.voice.channel
        
        try:
            # Get transcription channel
            channel_id = self.bot.transcription_channels[self.guild_id]
            transcript_channel = self.bot.get_channel(channel_id)
            
            # Connect to voice channel
            voice_client = await voice_channel.connect()
            
            # Create transcription sink
            sink = TranscriptionSink(self.bot, transcript_channel)
            self.bot.active_sessions[self.guild_id] = sink
            
            # Start recording
            voice_client.start_recording(sink, None, None)
            
            # Update buttons
            self.update_buttons()
            
            # Update the control panel
            await interaction.response.edit_message(
                embed=self.get_status_embed("🔴 Recording Active", voice_channel.name),
                view=self
            )
            
            # Send notification in transcript area
            notify_embed = discord.Embed(
                description=f"🔴 **Recording started** in {voice_channel.mention}",
                color=0xff0000,
                timestamp=discord.utils.utcnow()
            )
            await transcript_channel.send(embed=notify_embed)
            
        except Exception as e:
            logger.error(f"Error starting transcription: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while starting transcription. Check my permissions.",
                ephemeral=True
            )
    
    async def stop_callback(self, interaction: discord.Interaction):
        """Handle stop button click"""
        try:
            # Stop recording and disconnect
            voice_client = interaction.guild.voice_client
            if voice_client:
                voice_client.stop_recording()
                await voice_client.disconnect()
            
            # Cleanup session
            if self.guild_id in self.bot.active_sessions:
                sink = self.bot.active_sessions[self.guild_id]
                sink.cleanup()
                del self.bot.active_sessions[self.guild_id]
            
            # Update buttons
            self.update_buttons()
            
            # Update the control panel with suggestion
            await interaction.response.edit_message(
                embed=self.get_status_embed("⏹️ Recording Stopped", "Ready for next session"),
                view=self
            )
            
            # Send notification with clear suggestion
            channel_id = self.bot.transcription_channels[self.guild_id]
            transcript_channel = self.bot.get_channel(channel_id)
            if transcript_channel:
                notify_embed = discord.Embed(
                    description="⏹️ **Recording stopped**\n💡 *Tip: Click 'Clear Chat' to prepare for your next session*",
                    color=0x808080,
                    timestamp=discord.utils.utcnow()
                )
                await transcript_channel.send(embed=notify_embed)
                
        except Exception as e:
            logger.error(f"Error stopping transcription: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while stopping transcription.",
                ephemeral=True
            )
    
    async def clear_callback(self, interaction: discord.Interaction):
        """Handle clear button click"""
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ You need 'Manage Messages' permission to clear the chat.",
                ephemeral=True
            )
            return
        
        try:
            channel_id = self.bot.transcription_channels[self.guild_id]
            transcript_channel = self.bot.get_channel(channel_id)
            
            # Delete all messages except the control panel
            deleted_count = 0
            async for message in transcript_channel.history(limit=None):
                if message.embeds and message.embeds[0].title == "🎤 LeoScribeBot Control Panel":
                    continue  # Keep the control panel
                try:
                    await message.delete()
                    deleted_count += 1
                except:
                    pass  # Skip messages we can't delete
            
            await interaction.response.send_message(
                f"✅ Cleared {deleted_count} messages from the transcription channel!",
                ephemeral=True
            )
            
            # Send fresh welcome message
            welcome_embed = discord.Embed(
                title="🎤 LeoScribeBot Transcription Channel",
                description="Ready for voice chat transcription!\n\nUse the control panel above to start recording.",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            await transcript_channel.send(embed=welcome_embed)
            
        except Exception as e:
            logger.error(f"Error clearing messages: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while clearing messages.",
                ephemeral=True
            )
    
    def get_status_embed(self, status: str, details: str = "") -> discord.Embed:
        """Generate status embed for control panel"""
        embed = discord.Embed(
            title="🎤 LeoScribeBot Control Panel",
            color=0x3498db,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Status", value=status, inline=True)
        if details:
            embed.add_field(name="Details", value=details, inline=True)
        embed.add_field(name="Instructions", value="• Join a voice channel\n• Click Start to begin\n• Click Stop when done\n• Click Clear to reset", inline=False)
        return embed

class LeoScribeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        
        self.transcription_channels: Dict[int, int] = {}  # guild_id -> channel_id
        self.active_sessions: Dict[int, TranscriptionSink] = {}  # guild_id -> sink
        self.control_panels: Dict[int, int] = {}  # guild_id -> message_id
        
    async def setup_hook(self):
        """Called when the bot is starting up"""
        # Add persistent view for button handling
        self.add_view(TranscriptionView(self, 0))  # Dummy view for persistent handling
        await self.tree.sync()
        logger.info("Command tree synced!")
        
    async def on_ready(self):
        logger.info(f'{self.user} has connected to Discord!')
        self.check_for_silence.start()

    @tasks.loop(seconds=1)
    async def check_for_silence(self):
        """Check for users who have stopped talking and transcribe their audio"""
        for guild_id, sink in list(self.active_sessions.items()):
            if not sink.processing:
                continue
                
            for user_id, buffer in list(sink.user_buffers.items()):
                if buffer.is_silent() and buffer.audio_frames:
                    # User has stopped talking, transcribe their audio
                    audio_data = buffer.get_audio_data()
                    buffer.clear()
                    
                    # Run transcription in background
                    asyncio.create_task(sink.transcribe_and_send(user_id, audio_data))

    @check_for_silence.before_loop
    async def before_check_for_silence(self):
        await self.wait_until_ready()

# Create bot instance
bot = LeoScribeBot()

@bot.tree.command(name="setup", description="Create a dedicated channel with interactive controls")
async def setup_command(interaction: discord.Interaction):
    """Create a dedicated transcription channel with control panel"""
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "❌ You need 'Manage Channels' permission to use this command.",
            ephemeral=True
        )
        return
        
    guild = interaction.guild
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
        
        await interaction.response.send_message(
            f"✅ Using existing channel {existing_channel.mention} with fresh control panel!",
            ephemeral=True
        )
        return
    
    try:
        # Create the channel
        channel = await guild.create_text_channel(
            channel_name,
            topic="Real-time voice chat transcriptions by LeoScribeBot 🎤📝",
            reason="LeoScribeBot transcription channel"
        )
        
        bot.transcription_channels[guild.id] = channel.id
        
        # Create control panel
        view = TranscriptionView(bot, guild.id)
        embed = view.get_status_embed("⚪ Ready", "Channel created successfully")
        
        control_message = await channel.send(embed=embed, view=view)
        bot.control_panels[guild.id] = control_message.id
        
        # Send welcome message
        welcome_embed = discord.Embed(
            title="🎤 LeoScribeBot Transcription Channel",
            description="Welcome to your voice transcription channel!\n\nUse the control panel above to manage recording sessions.",
            color=0x00ff00
        )
        await channel.send(embed=welcome_embed)
        
        await interaction.response.send_message(
            f"✅ Created transcription channel {channel.mention} with interactive controls!",
            ephemeral=True
        )
        
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to create channels. Please check my permissions.",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Error creating channel: {e}")
        await interaction.response.send_message(
            "❌ An error occurred while creating the channel.",
            ephemeral=True
        )

# Error handling
@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command error: {error}")

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    bot.run(os.getenv('DISCORD_TOKEN'))
