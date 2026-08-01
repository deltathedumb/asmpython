# tier: spec
# ref: library/asyncio-runner.html
# expect:
# ValueError x
# handled
import asyncio

async def boom():
    raise ValueError("x")

try:
    asyncio.run(boom())
except ValueError as e:
    print("ValueError", e)

async def caught():
    try:
        await boom()
    except ValueError:
        return "handled"

print(asyncio.run(caught()))
