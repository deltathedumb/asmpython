# probes: async for drives an async generator
# expect:
# [1, 2, 3]
import asyncio


async def counter():
    for n in [1, 2, 3]:
        yield n


async def main():
    seen = []
    async for n in counter():
        seen.append(n)
    return seen


print(asyncio.run(main()))
