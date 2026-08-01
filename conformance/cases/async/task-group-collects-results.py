# tier: spec
# ref: library/asyncio-task.html#task-groups
# min-python: 3.11
# expect:
# [1, 2, 3]
import asyncio

async def val(v):
    await asyncio.sleep(0)
    return v

async def main():
    out = []
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(val(n)) for n in (1, 2, 3)]
    for t in tasks:
        out.append(t.result())
    return out

print(asyncio.run(main()))
