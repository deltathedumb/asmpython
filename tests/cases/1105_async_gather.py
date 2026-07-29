# expect:
# 60
import asyncio

async def val(n: int) -> int:
    await asyncio.sleep(0)
    return n * 10

async def go() -> int:
    rs = await asyncio.gather(val(1), val(2), val(3))
    t = 0
    for r in rs:
        t = t + r
    return t

print(asyncio.run(go()))
