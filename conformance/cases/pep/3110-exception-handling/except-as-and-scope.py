# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# accepted | except ValueError as e:
# accepted | except (ValueError, TypeError) as e:
# SyntaxError | except ValueError as (a, b):
# ValueError
# NameError
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    return "accepted"


for src in (
    "try:\n    pass\nexcept ValueError as e:\n    pass",
    "try:\n    pass\nexcept (ValueError, TypeError) as e:\n    pass",
    "try:\n    pass\nexcept ValueError as (a, b):\n    pass",
):
    print(check(src), "|", src.split(chr(10))[2].strip())

# The bound name does not survive the handler.
try:
    raise ValueError("x")
except ValueError as e:
    print(type(e).__name__)
try:
    e
except NameError:
    print("NameError")
