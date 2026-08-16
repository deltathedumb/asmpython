# expect:
# 5.0
# 9.0
# 10
# 1.5 < 2 < 2.5
# avg = 25
# -3
# 2.5
total = 0.0
total += 5.0
print(total)
total += 4
print(total)
total *= 1.2222222
# truncated by %g default precision: should display ~11
print(int(total))

if 1.5 < 2 < 2.5:
    print("1.5 < 2 < 2.5")
else:
    print("nope")

xs = 0
n = 0
xs += 10
n += 1
xs += 20
n += 1
xs += 30
n += 1
avg = xs / n
# avg is 20.0 -> 60/3 -> printed via int()
print("avg", "=", int(avg + 5))

# floor div with negatives
print(int(-2.5 // 1.0))   # -3

# String <-> float
s = str(2.5)
print(s)
