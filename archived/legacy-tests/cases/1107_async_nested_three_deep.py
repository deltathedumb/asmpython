# expect:
# 14
import asyncio

async def lvl3(n: int) -> int:
    return n + 1

async def lvl2(n: int) -> int:
    a = await lvl3(n)
    return a * 2

async def lvl1(n: int) -> int:
    b = await lvl2(n)
    c = await lvl2(b)
    return b + c

print(asyncio.run(lvl1(1)))
