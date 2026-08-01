# tier: spec
# ref: reference/expressions.html#asynchronous-generator-functions
# expect:
# [0, 1, 2]
import asyncio

async def agen():
    for i in range(3):
        yield i

async def main():
    out = []
    async for v in agen():
        out.append(v)
    return out

print(asyncio.run(main()))
