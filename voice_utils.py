# voice_utils.py
import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    """Raised when voice connection fails after retries"""
    pass

def ensure_opus_loaded():
    """Ensure Opus codec is loaded for voice functionality."""
    try:
        if not discord.opus.is_loaded():
            for lib in ('libopus.so.0', 'libopus.so', 'opus'):
                try:
                    discord.opus.load_opus(lib)
                    logger.info(f"Loaded Opus library: {lib}")
                    break
                except OSError:
                    continue
            else:
                logger.warning("Could not load Opus. Install libopus0 (apt) and ffmpeg.")
    except Exception as e:
        logger.warning(f"Opus loading failed: {e}")

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    """
    Hard-reset any existing voice, then connect with controlled retries/backoff.
    Handles common WS codes like 4006 (invalid session), 4009 (timeout), 4014 (kicked/perm change).
    """
    # Kill any old connection first
    if guild.voice_client:
        try:
            if hasattr(guild.voice_client, "stop_recording"):
                try:
                    guild.voice_client.stop_recording()
                except Exception:
                    pass
            await guild.voice_client.disconnect(force=True)
        except Exception as e:
            logger.warning(f"Error cleaning old voice connection: {e}")
        await asyncio.sleep(2.0)  # let Discord drop the old session

    delay = 1.0
    for attempt in range(1, 6):
        try:
            logger.info(f"Voice connect attempt {attempt} for guild '{guild.name}'")
            # IMPORTANT: your Pycord build doesn't accept self_deaf here
            vc = await channel.connect(timeout=15, reconnect=False)

            # Optional: self-deafen AFTER connecting (best-effort; not all builds support this)
            try:
                await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=False)
            except Exception:
                pass

            # Wait until ready
            for _ in range(40):  # up to ~10s
                if vc.is_connected():
                    logger.info(f"Voice connected successfully to {channel.name}")
                    return vc
                await asyncio.sleep(0.25)

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
