# tier: spec
# ref: reference/compound_stmts.html#mapping-patterns
# expect:
# a:1
# t=b rest=['x', 'y']
# any-mapping
# not-a-mapping
def f(v):
    match v:
        case {"type": "a", "value": val}:
            return f"a:{val}"
        case {"type": t, **rest}:
            return f"t={t} rest={sorted(rest)}"
        case {}:
            return "any-mapping"
        case _:
            return "not-a-mapping"

print(f({"type": "a", "value": 1}))
print(f({"type": "b", "x": 1, "y": 2}))
print(f({}))
print(f([1]))
