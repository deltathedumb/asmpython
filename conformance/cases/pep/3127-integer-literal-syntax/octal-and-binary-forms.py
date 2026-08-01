# tier: spec
# ref: reference/lexical_analysis.html#integer-literals
# expect:
# 15 10 31
# SyntaxError | 017
# accepted | 0o17
# accepted | 0b1010
# accepted | 0
# accepted | 00
# SyntaxError | 01
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    return "accepted"


print(0o17, 0b1010, 0x1f)
for src in ("017", "0o17", "0b1010", "0", "00", "01"):
    print(check(src), "|", src)
