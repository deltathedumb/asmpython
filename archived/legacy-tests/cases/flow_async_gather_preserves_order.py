# probes: gather returns results in argument order
# expect:
# [1, 2, 3]
import asyncio


async def value(n):
    await asyncio.sleep(0)
    return n


async def main():
    return await asyncio.gather(value(1), value(2), value(3))


print(asyncio.run(main()))
