# tier: spec
# ref: reference/simple_stmts.html#the-global-statement
# expect:
# 2 1
# 3
n = 1

def shadow():
    n = 2
    return n

def rebind():
    global n
    n = 3

print(shadow(), n)
rebind()
print(n)
