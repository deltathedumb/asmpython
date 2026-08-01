# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# ['else', 'finally']
# ['except', 'finally']
def f(boom):
    out = []
    try:
        if boom:
            raise ValueError("x")
    except ValueError:
        out.append("except")
    else:
        out.append("else")
    finally:
        out.append("finally")
    return out

print(f(False))
print(f(True))
