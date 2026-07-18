# ext: must_use
# expect-error: is discarded, but it is marked @must_use

@must_use
def square(n: int) -> int:
    return n * n

square(5)
