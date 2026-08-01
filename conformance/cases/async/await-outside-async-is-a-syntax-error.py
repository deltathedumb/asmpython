# tier: spec
# ref: reference/expressions.html#await
# expect:
# SyntaxError
# code
src = "await 1"
try:
    compile(src, "<t>", "exec")
except SyntaxError:
    print("SyntaxError")
compiled = compile("async def f():\n    return await g()", "<t>", "exec")
print(type(compiled).__name__)
