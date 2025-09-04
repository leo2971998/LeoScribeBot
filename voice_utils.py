# voice_utils.py (only the function shown—keep the rest as-is)
import asyncio
import logging
import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    pass

async def connect_voice_fresh(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    async def _hard_reset():
        vc = guild.voice_client
        if vc:
            try:
                if hasattr(vc, "stop_recording"):
                    try: vc.stop_recording()
                    except Exception: pass
                await vc.disconnect(force=True)
            except Exception as e:
                logger.warning(f"Error disconnecting existing voice client: {e}")
        # Also clear any lingering voice state on Discord's side
        try:
            await guild.change_voice_state(channel=None, self_mute=True, self_deaf=True)
        except Exception:
            pass
        await asyncio.sleep(2.5)  # longer cool-down helps 4006

    async def _reuse_or_move() -> discord.VoiceClient | None:
        vc = guild.voice_client
        if not vc:
            return None
        try:
            if vc.is_connected():
                if vc.channel and vc.channel.id == channel.id:
                    logger.info("Reusing existing voice connection")
                    return vc
                await vc.move_to(channel)
                for _ in range(20):  # ~5s
                    if vc.channel and vc.channel.id == channel.id:
                        logger.info(f"Moved voice to {channel.name}")
                        return vc
                    await asyncio.sleep(0.25)
                await _hard_reset()
                return None
            else:
                await _hard_reset()
                return None
        except Exception as e:
            logger.warning(f"Failed to reuse/move existing voice client: {e}")
            await _hard_reset()
            return None

    reused = await _reuse_or_move()
    if reused:
        return reused

    delay = 1.0
    for attempt in range(1, 6):
        try:
            logger.info(f"Voice connect attempt {attempt} for guild '{guild.name}'")

            reused = await _reuse_or_move()
            if reused:
                return reused

            vc = await channel.connect(timeout=15, reconnect=False)

            # Self-deafen after connecting (best-effort)
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

            try: await vc.disconnect(force=True)
            except Exception: pass
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
