# tier: spec
# ref: reference/expressions.html#asynchronous-generator-functions
# expect:
# [0, 1]
# ['cleanup']
import asyncio

log = []

async def agen():
    try:
        for i in range(5):
            yield i
    finally:
        log.append("cleanup")

async def main():
    out = []
    async for v in agen():
        out.append(v)
        if v == 1:
            break
    return out

print(asyncio.run(main()))
print(log)
