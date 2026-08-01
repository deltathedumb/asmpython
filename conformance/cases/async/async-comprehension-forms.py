# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# ([0, 1, 2], [0, 1, 2], [(0, 0), (1, 2)], [1])
import asyncio

async def agen(n):
    for i in range(n):
        yield i

async def main():
    lst = [v async for v in agen(3)]
    st = {v async for v in agen(3)}
    dct = {v: v * 2 async for v in agen(2)}
    gen = [v async for v in agen(2) if v]
    return lst, sorted(st), sorted(dct.items()), gen

print(asyncio.run(main()))
