# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# min-python: 3.14
# expect:
# caught-without-parens
# bound ValueError
# accepted | except ValueError, TypeError:
# SyntaxError | except ValueError, TypeError as e:
# accepted | except (ValueError, TypeError) as e:
# accepted | except* ValueError, TypeError:
try:
    raise TypeError("t")
except ValueError, TypeError:
    print("caught-without-parens")

try:
    raise ValueError("v")
except (ValueError, TypeError) as e:
    print("bound", type(e).__name__)


def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    return "accepted"


for src in (
    "try:\n    pass\nexcept ValueError, TypeError:\n    pass",
    "try:\n    pass\nexcept ValueError, TypeError as e:\n    pass",
    "try:\n    pass\nexcept (ValueError, TypeError) as e:\n    pass",
    "try:\n    pass\nexcept* ValueError, TypeError:\n    pass",
):
    print(check(src), "|", src.split(chr(10))[2].strip())
