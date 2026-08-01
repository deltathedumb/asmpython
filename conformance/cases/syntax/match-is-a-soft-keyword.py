# tier: spec
# ref: reference/compound_stmts.html#the-match-statement
# expect:
# accepted | match = 1
# accepted | match(1)
# accepted | match x:\n    case 1:\n        pass
# accepted | match x:\n    case _ if True:\n        pass
# accepted | match x:\n    case 1 | 2:\n        pass
# accepted | match x:\n    case [1, *_, 2]:\n        pass
# SyntaxError | match x:\n    case {**rest, 'a': 1}:\n        pass
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "match = 1",
    "match(1)",
    "match x:\n    case 1:\n        pass",
    "match x:\n    case _ if True:\n        pass",
    "match x:\n    case 1 | 2:\n        pass",
    "match x:\n    case [1, *_, 2]:\n        pass",
    "match x:\n    case {**rest, 'a': 1}:\n        pass",
):
    print(check(src), "|", src.replace("\n", "\\n"))
