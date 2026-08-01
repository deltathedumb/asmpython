# tier: spec
# ref: reference/simple_stmts.html#the-global-statement
# expect:
# 2
# 99 2
counter = 0

def outer():
    def inner():
        global counter
        counter += 1
    inner()
    inner()

outer()
print(counter)

def local_only():
    counter = 99
    return counter

print(local_only(), counter)
