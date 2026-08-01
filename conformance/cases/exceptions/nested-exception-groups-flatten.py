# tier: spec
# ref: library/exceptions.html#exception-groups
# min-python: 3.11
# expect:
# ['TypeError', 'ValueError']
# outer 2
inner = ExceptionGroup("inner", [ValueError("a")])
outer = ExceptionGroup("outer", [inner, TypeError("b")])
names = []

def walk(eg):
    for e in eg.exceptions:
        if isinstance(e, ExceptionGroup):
            walk(e)
        else:
            names.append(type(e).__name__)

walk(outer)
print(sorted(names))
print(outer.message, len(outer.exceptions))
