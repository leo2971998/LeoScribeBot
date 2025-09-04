# voice_utils.py
import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    """Raised when voice connection fails after retries."""
    pass

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
    Robustly connect to a voice channel, clearing any stale sessions.
    Handles common WS errors like 4006 (invalid session).
    """

    async def _hard_reset():
        """Forcefully drop any existing voice session and clear voice state."""
        vc = guild.voice_client
        if vc:
            try:
                if hasattr(vc, "stop_recording"):
                    try:
                        vc.stop_recording()
                    except Exception:
                        pass
                await vc.disconnect(force=True)
            except Exception as e:
                logger.warning(f"Error disconnecting existing voice client: {e}")

        # Also clear the guild’s voice state on Discord’s side
        try:
            await guild.change_voice_state(channel=None, self_mute=True, self_deaf=True)
        except Exception:
            pass

        # Give Discord time to fully drop the session
        await asyncio.sleep(2.5)

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
                # Wait up to ~5s for move to complete
                for _ in range(20):
                    if vc.channel and vc.channel.id == channel.id:
                        logger.info(f"Moved voice to {channel.name}")
                        return vc
                    await asyncio.sleep(0.25)
                # Move didn’t settle—reset and start fresh
                await _hard_reset()
                return None
            else:
                await _hard_reset()
                return None
        except Exception as e:
            logger.warning(f"Failed to reuse/move voice client: {e}")
            await _hard_reset()
            return None

    # Try to reuse/move first
    reused = await _reuse_or_move()
    if reused:
        return reused

    delay = 1.0
    attempts = 5

    for attempt in range(1, attempts + 1):
        try:
            logger.info(f"Voice connect attempt {attempt} for guild '{guild.name}'")

            # Re-check reuse in case state changed between attempts
            reused = await _reuse_or_move()
            if reused:
                return reused

            # Connect fresh (no self_deaf kw; set that right after)
            vc = await channel.connect(timeout=15, reconnect=False)

            # Deafen the bot after connect (best-effort)
            try:
                await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=False)
            except Exception:
                pass

            # Wait up to ~10s for a fully ready connection
            for _ in range(40):
                if vc.is_connected():
                    logger.info(f"Voice connected successfully to {channel.name}")
                    return vc
                await asyncio.sleep(0.25)

            # Connected but never became ready; drop and retry
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            raise VoiceConnectError("Voice connected but did not become ready in time")

        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code in (4006, 4009, 4014):
                logger.warning(f"Voice WS {code} on attempt {attempt}; hard-reset, backoff {delay:.1f}s…")
                await _hard_reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            logger.error(f"Voice ConnectionClosed (code={code}): {e}")
            raise

        except discord.ClientException as e:
            # Typical when a connection is half-open: “Already connected…”
            if "Already connected to a voice channel" in str(e):
                reused = await _reuse_or_move()
                if reused:
                    return reused
                await _hard_reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            raise

        except Exception as e:
            logger.warning(f"Voice connect attempt {attempt} failed: {e}; hard-reset, backoff {delay:.1f}s…")
            await _hard_reset()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)

    raise VoiceConnectError(f"Failed to connect to voice after {attempt} attempts")
