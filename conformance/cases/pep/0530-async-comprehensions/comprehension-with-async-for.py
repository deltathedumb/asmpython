# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# [0, 2, 4]
import asyncio

async def agen():
    for i in range(3):
        yield i

async def main():
    return [v * 2 async for v in agen()]

print(asyncio.run(main()))
