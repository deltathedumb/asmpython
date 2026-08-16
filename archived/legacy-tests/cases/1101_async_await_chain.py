# expect:
# 18
import asyncio

async def inner(n: int) -> int:
    return n * 2

async def outer() -> int:
    a = await inner(3)
    b = await inner(a)
    return a + b

print(asyncio.run(outer()))
