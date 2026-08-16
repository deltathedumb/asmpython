# expect:
# hello

from functools import lru_cache, wraps

@lru_cache(maxsize=128)
def greet(name: str) -> str:
    return "hello"

print(greet("world"))
