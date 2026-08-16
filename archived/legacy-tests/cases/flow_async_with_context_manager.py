# probes: async with runs __aenter__/__aexit__
# expect:
# aenter
# handle
# aexit
import asyncio


class Resource:
    async def __aenter__(self):
        print("aenter")
        return "handle"

    async def __aexit__(self, exc_type, exc, tb):
        print("aexit")
        return False


async def main():
    async with Resource() as handle:
        print(handle)


asyncio.run(main())
