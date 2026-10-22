import asyncio


async def close_voice_client(vc):
    if not vc:
        return
    try:
        async with asyncio.timeout(10):
            if not vc.is_connected() and getattr(vc, '_connection', None):
                # A kick has already consumed the channel=None confirmation.
                # Do not wait for a second confirmation that may never arrive.
                vc.stop()
                await vc._connection.disconnect(force=True, wait=False)
            else:
                await vc.disconnect(force=True)
    finally:
        guild = getattr(vc, 'guild', None)
        if guild and guild.voice_client is vc:
            vc.cleanup()
