# expect:
# 1-2-3
# a, b
# 123!
# x y.
# done

print(1, 2, 3, sep="-")           # 1-2-3
print("a", "b", sep=", ")         # a, b
print(1, 2, 3, sep="", end="!\n") # 123!
print("x", "y", end=".")          # x y.
print()                            # (blank line)
print("done")
