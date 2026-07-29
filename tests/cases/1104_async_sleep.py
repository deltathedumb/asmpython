# expect:
# 5
import asyncio

async def go() -> int:
    await asyncio.sleep(0)
    return 5

print(asyncio.run(go()))
