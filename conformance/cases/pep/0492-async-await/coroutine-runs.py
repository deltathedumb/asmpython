# tier: spec
# ref: reference/compound_stmts.html#coroutines
# expect:
# inner+outer
# coroutine
import asyncio

async def inner():
    return "inner"

async def main():
    v = await inner()
    return v + "+outer"

print(asyncio.run(main()))
print(type(main()).__name__)
