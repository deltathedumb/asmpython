# expect:
# 14
import asyncio

async def step(n: int) -> int:
    return n + 2

async def go() -> int:
    t = 0
    i = 0
    while i < 4:
        t = t + await step(i)
        i = i + 1
    return t

print(asyncio.run(go()))
