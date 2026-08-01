# tier: spec
# ref: reference/datamodel.html#asynchronous-iterators
# expect:
# [1, 2, 3]
import asyncio

class AIter:
    def __init__(self):
        self.i = 0
    def __aiter__(self):
        return self
    async def __anext__(self):
        if self.i >= 3:
            raise StopAsyncIteration
        self.i += 1
        return self.i

async def main():
    out = []
    async for v in AIter():
        out.append(v)
    return out

print(asyncio.run(main()))
