# voice_utils.py
import asyncio
import logging
from typing import Iterable, Optional, Set, Dict

import discord

logger = logging.getLogger(__name__)

class VoiceConnectError(Exception):
    """Raised when voice connection fails after retries."""
    pass

# ---- Problematic guild handling ------------------------------------------------

# Seed with the guild IDs you’ve seen repeatedly fail (you can add/remove safely).
# Example IDs from your logs:
#   Study:        884793798679482458
#   Happy Home:   1336548070187335703
PROBLEM_GUILDS: Set[int] = {
    884793798679482458,
    1336548070187335703,
}

# Prefer regions that we’ve seen succeed (HK often works when SIN/ATL fail).
PREFERRED_REGIONS: Dict[int, Iterable[str]] = {
    884793798679482458: ("hongkong", "us-south", "us-central"),   # Study
    1336548070187335703: ("us-south", "us-central", "hongkong"),  # Happy Home
}

# Fallback regions to try if a guild isn’t in the map above
DEFAULT_REGIONS: Iterable[str] = ("hongkong", "us-south", "us-central", "singapore")


# ---- Opus loader --------------------------------------------------------------

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
        logger.warning("Could not load Opus library. Install libopus0 (apt) if you need voice.")
    except Exception as e:
        logger.warning(f"Opus loading failed: {e}")


# ---- Internal helpers ---------------------------------------------------------

async def _hard_reset_voice_state(guild: discord.Guild):
    """Brutally clear any voice state on both client and Discord side."""
    # Disconnect current VC (if any)
    vc = guild.voice_client
    if vc is not None:
        try:
            if hasattr(vc, "stop_recording"):
                with suppress(Exception):
                    vc.stop_recording()
            await vc.disconnect(force=True)
        except Exception as e:
            logger.debug(f"While disconnecting old VC: {e}")

    # Clear voice state on the guild
    try:
        await guild.change_voice_state(channel=None, self_mute=True, self_deaf=True)
    except Exception as e:
        logger.debug(f"While clearing guild voice state: {e}")

    await asyncio.sleep(2.0)  # give Discord time to really drop the session


async def _bridge_endpoint_rotation(
    guild: discord.Guild,
    base_channel: discord.VoiceChannel,
    regions_to_try: Iterable[str],
) -> bool:
    """
    Force Discord to assign a different voice endpoint by briefly creating a
    temporary voice channel with a specific rtc_region and connecting to it.

    Returns True if we managed to perform at least one bridge connect.
    """
    me = guild.me or await guild.fetch_member(guild.client.user.id)
    perms = base_channel.permissions_for(me)
    if not perms.manage_channels:
        logger.debug("No Manage Channels; cannot run bridge endpoint rotation.")
        return False

    category = base_channel.category
    bridged = False

    for region in regions_to_try:
        try:
            tmp = await guild.create_voice_channel(
                name="leoscribe-bridge",
                rtc_region=region,
                user_limit=1,
                bitrate=min(64000, guild.bitrate_limit or 64000),
                category=category,
                reason=f"Endpoint rotation bridge (region={region})",
            )
        except Exception as e:
            logger.debug(f"Create bridge VC failed (region={region}): {e}")
            continue

        try:
            # Connect to the bridge channel to make Discord allocate an endpoint
            logger.info(f"Bridge connect to region '{region}'…")
            vc = await tmp.connect(timeout=20, reconnect=False)

            # Wait briefly; if we get here, the gateway accepted a fresh session
            for _ in range(10):
                if vc.is_connected():
                    break
                await asyncio.sleep(0.3)

            with suppress(Exception):
                await vc.disconnect(force=True)
            bridged = True
        except discord.errors.ConnectionClosed as e:
            logger.warning(f"Bridge connect failed (region={region}), WS code={getattr(e,'code',None)}")
        except Exception as e:
            logger.debug(f"Bridge connect error (region={region}): {e}")
        finally:
            with suppress(Exception):
                await tmp.delete(reason="Cleanup bridge VC")
            await asyncio.sleep(1.0)

    return bridged


class suppress:
    """Tiny context manager to suppress a given exception type (or all)."""
    def __init__(self, *exc_types):
        self.exc_types = exc_types or (Exception,)

    def __enter__(self):  # noqa: D401
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self.exc_types)


# ---- Public connect helper ----------------------------------------------------

async def connect_voice_fresh(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    *,
    base_attempts: int = 3,
    problem_attempts: int = 5,
) -> discord.VoiceClient:
    """
    Connect to `channel` with aggressive cleanup and recovery from 4006/4009/4014.

    On repeated 4006s, performs a "nuclear reset" and tries a bridge channel with
    region overrides to rotate the assigned endpoint. Guilds seen failing are
    tracked in PROBLEM_GUILDS for stronger handling.
    """
    # Always start with a hard reset to avoid lingering voice state
    await _hard_reset_voice_state(guild)

    # More attempts for guilds we've marked as problematic
    max_attempts = problem_attempts if guild.id in PROBLEM_GUILDS else base_attempts
    consecutive_4006 = 0

    # Region list to try for this guild if we need to rotate endpoints
    regions_to_try = PREFERRED_REGIONS.get(guild.id, DEFAULT_REGIONS)

    for attempt in range(1, max_attempts + 1):
        logger.info(f"Voice connect attempt {attempt}/{max_attempts} for guild '{guild.name}'")

        # If we’ve seen lots of 4006s, do a nuclear reset + bridge before retrying
        if consecutive_4006 >= 1:
            logger.warning(f"Applying nuclear reset for guild {guild.name}")
            await _hard_reset_voice_state(guild)
            with suppress(Exception):
                await _bridge_endpoint_rotation(guild, channel, regions_to_try)
            # After bridge, small pause before re-connecting
            await asyncio.sleep(1.0)

        try:
            vc = await channel.connect(timeout=20, reconnect=False)

            # Wait for ready; discord.py logs the handshake, but we poll readiness
            for _ in range(20):
                if vc.is_connected():
                    logger.info(f"Voice connected successfully to {channel.name}")
                    return vc
                await asyncio.sleep(0.25)

            # Connected but not “ready” fast enough, try again cleanly
            with suppress(Exception):
                await vc.disconnect(force=True)
            await asyncio.sleep(1.0)
            raise VoiceConnectError("Voice connected but not ready in time")

        except discord.errors.ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code == 4006:  # Invalid session
                consecutive_4006 += 1
                PROBLEM_GUILDS.add(guild.id)  # mark this guild for stronger handling next time
                logger.warning(f"Voice WS 4006 on attempt {attempt}; consecutive: {consecutive_4006}")
                # Loop will nuclear-reset/bridge before next attempt
                await asyncio.sleep(min(2.0 * consecutive_4006, 6.0))
                continue
            elif code in (4009, 4014):  # Session timeout / Voice channel kicked
                logger.warning(f"Voice WS {code} on attempt {attempt}; hard reset and retry")
                await _hard_reset_voice_state(guild)
                await asyncio.sleep(2.0)
                continue
            else:
                logger.error(f"Voice gateway closed (code={code})")
                raise

        except Exception as e:
            logger.warning(f"Voice connect attempt {attempt} failed: {e}")
            await asyncio.sleep(min(1.0 * attempt, 5.0))
            continue

    raise VoiceConnectError(f"Failed to connect to voice after {max_attempts} attempts")
