# expect:
# hi bob/hi amy
import asyncio

async def name(p: str) -> str:
    return "hi " + p

async def go() -> str:
    a = await name("bob")
    b = await name("amy")
    return a + "/" + b

print(asyncio.run(go()))
