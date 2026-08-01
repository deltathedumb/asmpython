# tier: spec
# ref: reference/compound_stmts.html#class-definitions
# expect:
# ['body']
# ['body']
# 1
log = []

class C:
    log.append("body")
    x = 1

print(log)
C()
C()
print(log)
print(C.x)
