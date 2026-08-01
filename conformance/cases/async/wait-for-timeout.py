# tier: spec
# ref: library/asyncio-task.html#asyncio.wait_for
# expect:
# TimeoutError
# True
import asyncio

async def slow():
    await asyncio.sleep(10)
    return "never"

async def main():
    try:
        await asyncio.wait_for(slow(), timeout=0.01)
    except asyncio.TimeoutError:
        return "TimeoutError"

print(asyncio.run(main()))
print(asyncio.TimeoutError is TimeoutError)
