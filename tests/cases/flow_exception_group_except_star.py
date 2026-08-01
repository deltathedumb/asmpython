# probes: except* handles an ExceptionGroup (3.11+)
# expect:
# values 1
# types 1
try:
    raise ExceptionGroup("group", [ValueError("a"), TypeError("b")])
except* ValueError as group:
    print("values", len(group.exceptions))
except* TypeError as group:
    print("types", len(group.exceptions))
