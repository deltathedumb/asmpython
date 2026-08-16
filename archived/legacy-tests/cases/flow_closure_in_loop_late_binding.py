# probes: closures made in a loop share the loop variable
# expect:
# [2, 2, 2]
fns = []
for i in range(3):
    fns.append(lambda: i)
print([f() for f in fns])
