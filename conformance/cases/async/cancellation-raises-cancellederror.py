# tier: spec
# ref: library/asyncio-task.html
# expect:
# ['cancelled', 'awaited-cancel']
import asyncio

log = []

async def slow():
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        log.append("cancelled")
        raise

async def main():
    task = asyncio.create_task(slow())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        log.append("awaited-cancel")
    return log

print(asyncio.run(main()))
