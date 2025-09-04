import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    pass

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    """
    Connect to (or reuse/move) a voice connection with robust cleanup between retries.
    Handles: 4006/4009/4014 + 'Already connected to a voice channel'.
    """

    async def _hard_reset():
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
        # let Discord drop the old session fully
        await asyncio.sleep(1.5)

    async def _reuse_or_move() -> discord.VoiceClient | None:
        """Reuse existing connection if suitable; move to target if possible."""
        vc = guild.voice_client
        if not vc:
            return None
        try:
            if vc.is_connected():
                if vc.channel and vc.channel.id == channel.id:
                    logger.info("Reusing existing voice connection")
                    return vc
                # move to target channel
                await vc.move_to(channel)
                # wait briefly for state to reflect
                for _ in range(20):  # ~5s
                    if vc.channel and vc.channel.id == channel.id:
                        logger.info(f"Moved voice to {channel.name}")
                        return vc
                    await asyncio.sleep(0.25)
                # move didn't settle; reset
                await _hard_reset()
                return None
            else:
                # not connected; reset
                await _hard_reset()
                return None
        except Exception as e:
            logger.warning(f"Failed to reuse/move existing voice client: {e}")
            await _hard_reset()
            return None

    # Try a quick reuse/move before entering the loop
    reused = await _reuse_or_move()
    if reused:
        return reused

    delay = 1.0
    for attempt in range(1, 6):
        try:
            logger.info(f"Voice connect attempt {attempt} for guild '{guild.name}'")

            # If something attached meanwhile, try reuse/move first
            reused = await _reuse_or_move()
            if reused:
                return reused

            # Fresh connect
            vc = await channel.connect(timeout=15, reconnect=False)

            # Optional: self-deafen after connecting (best-effort; not all builds)
            try:
                await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=False)
            except Exception:
                pass

            # Wait until ready
            for _ in range(40):  # ~10s
                if vc.is_connected():
                    logger.info(f"Voice connected successfully to {channel.name}")
                    return vc
                await asyncio.sleep(0.25)

            # Connected but not ready—reset and retry
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            raise VoiceConnectError("Voice connected but did not become ready in time")

        except discord.ClientException as e:
            # Typical when a VoiceClient is lingering: "Already connected to a voice channel."
            if "Already connected to a voice channel" in str(e):
                reused = await _reuse_or_move()
                if reused:
                    return reused
                # If we couldn't reuse/move, hard reset and retry
                await _hard_reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            raise

        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code in (4006, 4009, 4014):
                logger.warning(f"Voice WS {code} on attempt {attempt}; backoff {delay:.1f}s…")
                # Important: kill any half-open voice before retrying
                await _hard_reset()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            logger.error(f"Voice ConnectionClosed (code={code}): {e}")
            raise

        except Exception as e:
            logger.warning(f"Voice connect attempt {attempt} failed: {e}; backoff {delay:.1f}s…")
            # Clean up any partial attachment before retrying
            await _hard_reset()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)

    raise VoiceConnectError(f"Failed to connect to voice after {attempt} attempts")
