# expect:
# 10
import asyncio

async def ready(n: int) -> int:
    return n

async def go() -> int:
    if await ready(1) > 0:
        return 10
    return 20

print(asyncio.run(go()))
