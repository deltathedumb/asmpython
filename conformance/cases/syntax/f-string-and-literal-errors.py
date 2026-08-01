# tier: spec
# ref: reference/lexical_analysis.html#f-strings
# expect:
# SyntaxError | f"{}"
# SyntaxError | f"{1+}"
# SyntaxError | f"{x!z}"
# accepted | f"{x!r}"
# SyntaxError | "unterminated
# SyntaxError | 0x
# SyntaxError | 1_
# accepted | 1_000
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    'f"{}"',
    'f"{1+}"',
    'f"{x!z}"',
    'f"{x!r}"',
    '"unterminated',
    "0x",
    "1_",
    "1_000",
):
    print(check(src), "|", src)
