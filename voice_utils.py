# voice_utils.py - Nuclear session reset version
import asyncio
import logging
import discord
import aiohttp

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    """Raised when voice connection fails after retries."""
    pass

def ensure_opus_loaded():
    """Ensure the Opus codec is available for voice features."""
    try:
        if discord.opus.is_loaded():
            return
        candidates = ["libopus.so.0", "libopus.so", "opus"]
        for lib in candidates:
            try:
                discord.opus.load_opus(lib)
                logger.info(f"Loaded Opus library: {lib}")
                return
            except OSError:
                continue
        logger.warning("Could not load Opus library.")
    except Exception as e:
        logger.warning(f"Opus loading failed: {e}")

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    """Nuclear voice connection with complete session reset."""
    
    async def nuclear_reset():
        """Complete voice session destruction."""
        logger.info("Performing nuclear voice session reset...")
        
        # 1. Disconnect any existing voice client
        if guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
            except:
                pass
        
        # 2. Clear voice state multiple ways
        try:
            await guild.change_voice_state(channel=None, self_mute=True, self_deaf=True)
        except:
            pass
        
        # 3. Force websocket reconnection by changing presence
        try:
            bot = guild.me._state._get_client()  # Get bot instance
            await bot.change_presence(status=discord.Status.idle)
            await asyncio.sleep(1)
            await bot.change_presence(status=discord.Status.online)
        except:
            pass
        
        # 4. Wait for Discord to clear session
        await asyncio.sleep(5)
    
    # Start with nuclear reset
    await nuclear_reset()
    
    # Try connection with increasing delays
    delays = [2, 5, 10]  # Progressive backoff
    
    for attempt, delay in enumerate(delays, 1):
        try:
            logger.info(f"Voice connect attempt {attempt}/{len(delays)} after {delay}s delay")
            await asyncio.sleep(delay)
            
            # Fresh connection attempt
            vc = await channel.connect(timeout=20, reconnect=False)
            
            # Extended readiness check
            for check in range(30):  # 15 seconds
                if vc.is_connected():
                    # Additional verification - try to change voice state
                    try:
                        await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=False)
                        logger.info(f"Voice connected successfully to {channel.name}")
                        return vc
                    except Exception as e:
                        logger.warning(f"Voice state change failed: {e}")
                        # Connection exists but might be unstable, continue checking
                        
                await asyncio.sleep(0.5)
            
            # Connection didn't stabilize
            logger.warning(f"Voice connection unstable on attempt {attempt}")
            await vc.disconnect(force=True)
            
        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            logger.error(f"Voice ConnectionClosed (code={code}) on attempt {attempt}: {e}")
            if code == 4006 and attempt < len(delays):
                # For 4006, do another nuclear reset
                await nuclear_reset()
                continue
            elif attempt == len(delays):
                break  # Last attempt, will raise below
        
        except Exception as e:
            logger.error(f"Voice connect attempt {attempt} failed: {e}")
            if attempt < len(delays):
                await nuclear_reset()
                continue
    
    raise VoiceConnectError(f"Failed to connect to voice after {len(delays)} attempts with nuclear resets")
