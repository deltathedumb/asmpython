# tier: spec
# ref: reference/simple_stmts.html#the-del-statement
# expect:
# 1
# NameError
x = 1
print(x)
del x
try:
    print(x)
except NameError:
    print("NameError")
