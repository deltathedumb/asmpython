# tier: spec
# ref: library/asyncio-task.html
# expect:
# [3, 1, 2]
import asyncio

async def val(v):
    await asyncio.sleep(0)
    return v

async def main():
    return await asyncio.gather(val(3), val(1), val(2))

print(asyncio.run(main()))
