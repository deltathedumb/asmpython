# expect:
# 20
# 30
# 40
# ---
# 10
# 20
# 30
# ---
# c
# d
# ---
# len: 5
# slice len: 2
# 30
# 40

xs = [10, 20, 30, 40, 50]

# Full slice with both endpoints.
for v in xs[1:4]:
    print(v)
print("---")

# Open start.
for v in xs[:3]:
    print(v)
print("---")

# Open stop on a list[str].
names = ["a", "b", "c", "d"]
for n in names[2:]:
    print(n)
print("---")

# Make sure the source list is untouched.
print("len:", len(xs))
sub = xs[2:4]
print("slice len:", len(sub))
print(sub[0])
print(sub[1])
