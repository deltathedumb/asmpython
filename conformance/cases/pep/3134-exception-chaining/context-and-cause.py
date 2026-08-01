# tier: spec
# ref: reference/simple_stmts.html#the-raise-statement
# expect:
# ('ValueError', None)
# ('ValueError', True)
def implicit():
    try:
        try:
            raise ValueError("inner")
        except ValueError:
            raise KeyError("outer")
    except KeyError as e:
        return type(e.__context__).__name__, e.__cause__

def explicit():
    try:
        try:
            raise ValueError("inner")
        except ValueError as v:
            raise KeyError("outer") from v
    except KeyError as e:
        return type(e.__cause__).__name__, e.__suppress_context__

print(implicit())
print(explicit())
