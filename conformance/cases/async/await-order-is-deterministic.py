# tier: spec
# ref: reference/compound_stmts.html#coroutines
# expect:
# 3
# [('start', 1), ('end', 1), ('start', 2), ('end', 2)]
import asyncio

log = []

async def step(n):
    log.append(("start", n))
    await asyncio.sleep(0)
    log.append(("end", n))
    return n

async def main():
    a = await step(1)
    b = await step(2)
    return a + b

print(asyncio.run(main()))
print(log)
