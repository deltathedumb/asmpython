# probe118: complex function parameter patterns
# Keyword-only args (after *)
def config(name: str, *, verbose: bool = False, limit: int = 10) -> str:
    parts = [name]
    if verbose:
        parts.append("verbose")
    parts.append(str(limit))
    return " ".join(parts)

print(config("test"))                        # test 10
print(config("test", verbose=True))          # test verbose 10
print(config("test", limit=5))              # test 5
print(config("run", verbose=True, limit=3)) # run verbose 3

# Positional-only args (before /)
# Python 3.8+ feature - may not be supported
# def pos_only(x: int, y: int, /) -> int:
#     return x + y

# Mixed args
def mixed(a: int, b: int = 0, *args, key: str = "", **kwargs) -> str:
    result = str(a) + " " + str(b)
    if args:
        result += " args=" + str(len(args))
    if key:
        result += " key=" + key
    return result

print(mixed(1))                    # 1 0
print(mixed(1, 2))                 # 1 2
print(mixed(1, 2, 3, 4, key="x")) # 1 2 args=2 key=x
