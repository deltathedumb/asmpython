# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# tagged:body
# lambda:g
decorators = {"tag": lambda f: (lambda: "tagged:" + f())}

@decorators["tag"]
def f():
    return "body"

print(f())

@(lambda fn: (lambda: "lambda:" + fn()))
def g():
    return "g"

print(g())
