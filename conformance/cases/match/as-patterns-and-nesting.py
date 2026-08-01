# tier: spec
# ref: reference/compound_stmts.html#as-patterns
# expect:
# starts-with-1:[1, 2, 3]
# nested:[7, 8] a=7 b=8
# scalar:s
# scalar:4
# other
def f(v):
    match v:
        case [1, *_] as whole:
            return f"starts-with-1:{whole}"
        case {"k": [a, b] as inner}:
            return f"nested:{inner} a={a} b={b}"
        case (str() | int()) as scalar:
            return f"scalar:{scalar}"
        case _:
            return "other"

print(f([1, 2, 3]))
print(f({"k": [7, 8]}))
print(f("s"))
print(f(4))
print(f(1.5))
