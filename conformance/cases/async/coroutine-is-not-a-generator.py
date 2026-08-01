# tier: spec
# ref: reference/datamodel.html#coroutine-objects
# expect:
# True False
# True False
# True
# 1
import asyncio
import inspect

async def coro():
    return 1

def gen():
    yield 1

c = coro()
print(inspect.iscoroutine(c), inspect.isgenerator(c))
print(inspect.isgenerator(gen()), inspect.iscoroutine(gen()))
print(inspect.iscoroutinefunction(coro))
print(asyncio.run(c))
