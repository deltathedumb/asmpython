# expect:
# 6
# 16
import asyncio

async def val(n: int) -> int:
    return n * 3

async def go(flag: int) -> int:
    if flag > 0:
        r = await val(2)
        return r
    r2 = await val(5)
    return r2 + 1

print(asyncio.run(go(1)))
print(asyncio.run(go(0)))
