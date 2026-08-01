# probes: await suspends and resumes through a chain
# expect:
# outer+inner
import asyncio


async def inner():
    return "inner"


async def outer():
    value = await inner()
    return "outer+" + value


print(asyncio.run(outer()))
