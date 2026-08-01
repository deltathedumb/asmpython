# tier: cpython
# ref: reference/compound_stmts.html#coroutines
# expect:
# []
# coroutine
# ['ran']
import asyncio

log = []

async def never():
    log.append("ran")

c = never()
print(log)
print(type(c).__name__)
asyncio.run(c)
print(log)
