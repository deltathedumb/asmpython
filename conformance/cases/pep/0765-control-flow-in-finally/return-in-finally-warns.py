# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# min-python: 3.14
# expect:
# ['SyntaxWarning']
# ['SyntaxWarning']
# []
import warnings

def compiled(src):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(src, "<case>", "exec")
        return sorted({w.category.__name__ for w in caught})

print(compiled("def f():\n    try:\n        pass\n    finally:\n        return 1"))
print(compiled("def f():\n    for i in []:\n        try:\n            pass\n        finally:\n            break"))
print(compiled("def f():\n    try:\n        pass\n    finally:\n        pass"))
