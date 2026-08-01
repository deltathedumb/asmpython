# tier: spec
# ref: reference/datamodel.html#asynchronous-context-managers
# expect:
# ['aenter', ('body', 'value'), 'aexit']
import asyncio

log = []

class ACM:
    async def __aenter__(self):
        log.append("aenter")
        return "value"
    async def __aexit__(self, *a):
        log.append("aexit")
        return False

async def main():
    async with ACM() as v:
        log.append(("body", v))
    return log

print(asyncio.run(main()))
