# probes: await asyncio.sleep yields to the loop
# expect:
# before
# after
import asyncio


async def main():
    print("before")
    await asyncio.sleep(0)
    print("after")


asyncio.run(main())
