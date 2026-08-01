# tier: spec
# ref: reference/compound_stmts.html#the-while-statement
# expect:
# else 2
# done 1
n = 0
while n < 2:
    n += 1
else:
    print("else", n)

n = 0
while n < 2:
    n += 1
    break
else:
    print("unreachable")
print("done", n)
