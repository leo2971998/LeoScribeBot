import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    pass

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    """Hard-reset any existing voice, then connect with controlled retries/backoff."""
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
            # IMPORTANT: no self_deaf here (not supported in your build)
            vc = await channel.connect(timeout=15, reconnect=False)

            # Optionally self-deafen AFTER connecting (best-effort)
            try:
                await guild.change_voice_state(channel=channel, self_deaf=True, self_mute=False)
            except Exception:
                pass

            # Wait for ready
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
