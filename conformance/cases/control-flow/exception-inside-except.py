# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# ['handling', ('caught', 'KeyError'), ('context', 'ValueError')]
log = []
try:
    try:
        raise ValueError("first")
    except ValueError:
        log.append("handling")
        raise KeyError("second")
except KeyError as e:
    log.append(("caught", type(e).__name__))
    log.append(("context", type(e.__context__).__name__))
print(log)
