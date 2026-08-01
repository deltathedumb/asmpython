# tier: spec
# ref: reference/expressions.html#calls
# expect:
# 1
# ['pick', 'arg']
log = []

def pick():
    log.append("pick")
    return lambda v: v

def arg():
    log.append("arg")
    return 1

print(pick()(arg()))
print(log)
