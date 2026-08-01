# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# ['body', 'except', 'finally']
log = []
try:
    log.append("body")
    raise ValueError("x")
except ValueError:
    log.append("except")
else:
    log.append("else")
finally:
    log.append("finally")
print(log)
