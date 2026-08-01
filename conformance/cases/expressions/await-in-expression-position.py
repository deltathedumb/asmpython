# tier: spec
# ref: reference/expressions.html#await
# expect:
# (3, [1, 2])
import asyncio

async def value(v):
    return v

async def main():
    total = await value(1) + await value(2)
    xs = [await value(v) for v in (1, 2)]
    return total, xs

print(asyncio.run(main()))
