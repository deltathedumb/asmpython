# tier: spec
# ref: reference/compound_stmts.html#the-for-statement
# expect:
# no-break
# done
for i in range(3):
    pass
else:
    print("no-break")

for i in range(3):
    if i == 1:
        break
else:
    print("unreachable")
print("done")
