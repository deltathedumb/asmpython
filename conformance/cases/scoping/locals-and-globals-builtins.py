# tier: spec
# ref: reference/executionmodel.html#naming-and-binding
# expect:
# (['loc'], 'global', 1)
# True
# True
g = "global"

def f():
    loc = "local"
    return sorted(locals()), g, len([1])

print(f())
print("g" in globals())
print("len" in dir(__builtins__) or hasattr(__builtins__, "len"))
