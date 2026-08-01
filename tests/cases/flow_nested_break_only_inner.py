# probes: break leaves only the innermost loop
# expect:
# 1 1
# 2 1
# done
for outer in [1, 2]:
    for inner in [1, 2, 3]:
        if inner == 2:
            break
        print(outer, inner)
print("done")
