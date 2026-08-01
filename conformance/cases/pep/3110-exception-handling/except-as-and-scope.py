# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# accepted | except ValueError as e:
# accepted | except ValueError, e:
# accepted | except (ValueError, TypeError) as e:
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    return "accepted"


for src in (
    "try:\n    pass\nexcept ValueError as e:\n    pass",
    "try:\n    pass\nexcept ValueError, e:\n    pass",
    "try:\n    pass\nexcept (ValueError, TypeError) as e:\n    pass",
):
    print(check(src), "|", src.split(chr(10))[2].strip())
