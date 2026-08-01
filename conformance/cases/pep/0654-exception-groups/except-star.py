# tier: spec
# ref: reference/compound_stmts.html#except-star
# min-python: 3.11
# expect:
# [('TypeError', 1), ('ValueError', 1)]
caught = []
try:
    raise ExceptionGroup("g", [ValueError("v"), TypeError("t")])
except* ValueError as eg:
    caught.append(("ValueError", len(eg.exceptions)))
except* TypeError as eg:
    caught.append(("TypeError", len(eg.exceptions)))

print(sorted(caught))
