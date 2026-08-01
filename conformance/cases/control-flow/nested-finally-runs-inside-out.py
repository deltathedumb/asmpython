# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# ['body', 'inner-finally', 'handler', 'outer-finally']
log = []
try:
    try:
        log.append("body")
        raise ValueError("x")
    finally:
        log.append("inner-finally")
except ValueError:
    log.append("handler")
finally:
    log.append("outer-finally")
print(log)
