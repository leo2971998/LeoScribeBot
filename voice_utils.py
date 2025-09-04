import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    """Raised when voice connection fails after retries"""
    pass

def ensure_opus_loaded():
    """Ensure Opus codec is loaded for voice functionality"""
    try:
        if not discord.opus.is_loaded():
            # Try common names for the Opus shared library on Linux
            for lib in ('libopus.so.0', 'libopus.so', 'opus'):
                try:
                    discord.opus.load_opus(lib)
                    logger.info(f"Loaded Opus library: {lib}")
                    break
                except OSError:
                    continue
            else:
                logger.warning("Could not load Opus library. Install libopus0 (apt) and ffmpeg for voice support.")
    except Exception as e:
        logger.warning(f"Opus loading failed: {e}")

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    """
    Connect to voice channel with fresh session and retry logic for invalid voice sessions.
    Handles common WS codes like 4006 (invalid session), 4009 (timeout), 4014 (kicked/perm change).
    """
    # Hard reset any existing connection
    if guild.voice_client:
        try:
            if hasattr(guild.voice_client, "stop_recording"):
                try:
                    guild.voice_client.stop_recording()
                except Exception:
                    pass
            await guild.voice_client.disconnect(force=True)
        except Exception as e:
            logger.warning(f"Error cleaning up old voice connection: {e}")
        await asyncio.sleep(2.0)  # give time for Discord to drop session

    delay = 1.0
    for attempt in range(1, 6):  # up to 5 attempts
        try:
            logger.info(f"Voice connect attempt {attempt} for guild '{guild.name}'")
            # Disable library reconnect; we control retries here.
            vc = await channel.connect(timeout=15, reconnect=False, self_deaf=True)

            # Wait for connection to become ready
            for _ in range(40):  # up to ~10s
                if vc.is_connected():
                    logger.info(f"Voice connected successfully to {channel.name}")
                    return vc
                await asyncio.sleep(0.25)

            # Connected but didn't become ready in time
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            raise VoiceConnectError("Voice connected but did not become ready in time")

        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code in (4006, 4009, 4014):
                logger.warning(f"Voice WS {code} on attempt {attempt}; backoff {delay:.1f}s…")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            logger.error(f"Voice ConnectionClosed (code={code}): {e}")
            raise

        except Exception as e:
            logger.warning(f"Voice connect attempt {attempt} failed: {e}; backoff {delay:.1f}s…")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)

    raise VoiceConnectError(f"Failed to connect to voice after {attempt} attempts")
