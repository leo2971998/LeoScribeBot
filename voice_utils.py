# voice_utils.py
import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    pass

def ensure_opus_loaded():
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
                logger.warning("Could not load Opus. Install libopus0 + ffmpeg.")
    except Exception as e:
        logger.warning(f"Opus loading failed: {e}")

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    """Hard-reset any existing voice, then connect with controlled retries/backoff."""
    # 0) Nuke any existing connection first
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
        # Give Discord time to fully drop the old session
        await asyncio.sleep(2.0)

    delay = 1.0
    for attempt in range(1, 6):
        try:
            logger.info(f"Voice connect attempt {attempt} in guild '{guild.name}'")
            # IMPORTANT: disable internal reconnect loop; we handle retries here
            vc = await channel.connect(timeout=15, reconnect=False, self_deaf=True)

            # Wait for ready
            # Some environments need a moment after the WS handshake to be 'connected'
            for _ in range(40):  # up to ~10s
                if vc.is_connected():
                    logger.info(f"Voice connected to {channel.name}")
                    return vc
                await asyncio.sleep(0.25)

            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            raise VoiceConnectError("Voice connected but did not become ready in time")

        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            # 4006 invalid session, 4009 session timeout, 4014 kicked/permissions changes
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
