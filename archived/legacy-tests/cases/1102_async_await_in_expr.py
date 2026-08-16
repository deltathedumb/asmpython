# expect:
# 3
import asyncio

async def val(n: int) -> int:
    return n

async def go() -> int:
    total = await val(1) + await val(2)
    return total

print(asyncio.run(go()))
