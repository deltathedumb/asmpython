# probes: asyncio.run returns the coroutine's result
# expect:
# 42
import asyncio


async def compute():
    return 21 * 2


print(asyncio.run(compute()))
