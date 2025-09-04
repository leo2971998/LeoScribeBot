# voice_utils.py - Guild-specific 4006 handling
import asyncio
import logging
import discord
from contextlib import suppress

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    """Raised when voice connection fails after retries."""
    pass

# Track problematic guilds that need special handling
PROBLEMATIC_GUILDS = {
    884793798679482458,  # Study guild ID from your logs
}

def ensure_opus_loaded():
    """Ensure the Opus codec is available for voice features."""
    try:
        if discord.opus.is_loaded():
            return
        # Try common library names/paths
        candidates = ["libopus.so.0", "libopus.so", "opus"]
        for lib in candidates:
            try:
                discord.opus.load_opus(lib)
                logger.info(f"Loaded Opus library: {lib}")
                return
            except OSError:
                continue
        logger.warning(
            "Could not load Opus library. Install it on your system "
            "(e.g., `sudo apt-get install -y libopus0`)."
        )
    except Exception as e:
        logger.warning(f"Opus loading attempt failed: {e}")

async def connect_voice_fresh(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
) -> discord.VoiceClient:
    """
    Robustly connect to a voice channel with guild-specific handling.
    Special handling for guilds with persistent 4006 issues.
    """

    async def _hard_reset():
        """Forcefully drop any existing voice session and clear voice state."""
        vc = guild.voice_client
        if vc:
            try:
                if hasattr(vc, "stop_recording"):
                    with suppress(Exception):
                        vc.stop_recording()
                await vc.disconnect(force=True)
            except Exception as e:
                logger.warning(f"Error disconnecting existing voice client: {e}")

        # Clear voice state on Discord's side
        with suppress(Exception):
            await guild.change_voice_state(channel=None, self_mute=True, self_deaf=True)

        # Extra delay for problematic guilds
        delay = 5.0 if guild.id in PROBLEMATIC_GUILDS else 2.5
        await asyncio.sleep(delay)

    async def _reuse_or_move() -> discord.VoiceClient | None:
        """If already connected, reuse or move to the requested channel."""
        vc = guild.voice_client
        if not vc:
            return None
        try:
            if vc.is_connected():
                if vc.channel and vc.channel.id == channel.id:
                    logger.info("Reusing existing voice connection")
                    return vc
                await vc.move_to(channel)
                # Wait for move to complete
                for _ in range(20):
                    if vc.channel and vc.channel.id == channel.id:
                        logger.info(f"Moved voice to {channel.name}")
                        return vc
                    await asyncio.sleep(0.25)
                # Move didn't settle—reset and start fresh
                await _hard_reset()
                return None
            else:
                await _hard_reset()
                return None
        except Exception as e:
            logger.warning(f"Failed to reuse/move voice client: {e}")
            await _hard_reset()
            return None

    async def _nuclear_reset_for_problematic_guild():
        """Extreme measures for guilds with persistent 4006 issues."""
        logger.warning(f"Applying nuclear reset for problematic guild {guild.name}")
        
        # Multiple hard resets with increasing delays
        for i in range(3):
            await _hard_reset()
            await asyncio.sleep(3 + i * 2)
        
        # Try to force a different endpoint by changing bot presence
        try:
            bot = channel.guild.me._state._get_client()
            current_activity = bot.activity
            
            # Cycle through different statuses to potentially get a different endpoint
            await bot.change_presence(status=discord.Status.idle, activity=None)
            await asyncio.sleep(2)
            await bot.change_presence(status=discord.Status.dnd, activity=None)
            await asyncio.sleep(2)
            await bot.change_presence(status=discord.Status.online, activity=current_activity)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Presence cycling failed: {e}")

    # Check if this is a problematic guild
    is_problematic = guild.id in PROBLEMATIC_GUILDS
    if is_problematic:
        logger.info(f"Using special handling for problematic guild: {guild.name}")

    # Try to reuse/move first
    reused = await _reuse_or_move()
    if reused:
        return reused

    # Set parameters based on guild type
    if is_problematic:
        attempts = 3  # Fewer attempts but more aggressive resets
        base_delay = 2.0
        max_delay = 12.0
    else:
        attempts = 5
        base_delay = 1.0
        max_delay = 8.0

    delay = base_delay
    consecutive_4006 = 0

    for attempt in range(1, attempts + 1):
        try:
            logger.info(f"Voice connect attempt {attempt}/{attempts} for guild '{guild.name}'")

            # Re-check reuse in case state changed between attempts
            reused = await _reuse_or_move()
            if reused:
                return reused

            # For problematic guilds on 4006 streaks, do nuclear reset
            if is_problematic and consecutive_4006 >= 2:
                await _nuclear_reset_for_problematic_guild()
                consecutive_4006 = 0

            # Connect fresh with longer timeout for problematic guilds
            timeout = 25 if is_problematic else 15
            vc = await channel.connect(timeout=timeout, reconnect=False)

            # Deafen the bot after connect
            with suppress(Exception):
                await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=False)

            # Extended wait for problematic guilds
            check_iterations = 60 if is_problematic else 40
            for _ in range(check_iterations):
                if vc.is_connected():
                    logger.info(f"Voice connected successfully to {channel.name}")
                    consecutive_4006 = 0  # Reset counter on success
                    return vc
                await asyncio.sleep(0.25)

            # Connected but never became ready
            with suppress(Exception):
                await vc.disconnect(force=True)
            raise VoiceConnectError("Voice connected but did not become ready in time")

        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code in (4006, 4009, 4014):
                consecutive_4006 += 1
                logger.warning(f"Voice WS {code} on attempt {attempt}; consecutive: {consecutive_4006}")
                
                if is_problematic and code == 4006:
                    # For problematic guilds with 4006, skip to nuclear reset
                    await _nuclear_reset_for_problematic_guild()
                else:
                    await _hard_reset()
                
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
                continue

            logger.error(f"Voice ConnectionClosed (code={code}): {e}")
            raise

        except discord.ClientException as e:
            if "Already connected to a voice channel" in str(e):
                reused = await _reuse_or_move()
                if reused:
                    return reused
                await _hard_reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
                continue
            raise

        except Exception as e:
            logger.warning(f"Voice connect attempt {attempt} failed: {e}")
            if is_problematic:
                await _nuclear_reset_for_problematic_guild()
            else:
                await _hard_reset()
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

    # Mark this guild as problematic if it wasn't already
    if guild.id not in PROBLEMATIC_GUILDS:
        logger.error(f"Adding guild {guild.name} to problematic guilds list")
        PROBLEMATIC_GUILDS.add(guild.id)

    raise VoiceConnectError(f"Failed to connect to voice after {attempts} attempts")
