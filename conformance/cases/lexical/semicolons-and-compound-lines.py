# tier: spec
# ref: reference/compound_stmts.html
# expect:
# 3
# inline-if
# 0
# 1
# inline-def
# done
a = 1; b = 2; print(a + b)
if True: print("inline-if")
for i in range(2): print(i)
def f(): return "inline-def"
print(f())
while False: pass
print("done")
