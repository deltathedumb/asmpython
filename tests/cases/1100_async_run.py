# expect:
# 1
import asyncio

async def one() -> int:
    return 1

print(asyncio.run(one()))
