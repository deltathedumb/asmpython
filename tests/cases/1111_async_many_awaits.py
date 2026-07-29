# expect:
# 15
import asyncio

async def v(n: int) -> int:
    return n

async def go() -> int:
    a = await v(1)
    b = await v(2)
    c = await v(3)
    d = await v(4)
    e = await v(5)
    return a + b + c + d + e

print(asyncio.run(go()))
