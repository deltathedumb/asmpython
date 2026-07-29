# expect:
# 14
import asyncio

async def val(n: int) -> int:
    return n * n

async def go() -> int:
    total = 0
    for i in range(4):
        total = total + await val(i)
    return total

print(asyncio.run(go()))
