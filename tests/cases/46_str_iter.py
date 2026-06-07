# expect:
# h
# e
# l
# l
# o
# 5
# 3
for ch in "hello":
    print(ch)

# Same string iterated again, count chars manually.
n = 0
for ch in "hello":
    n = n + 1
print(n)

# Iterate over a variable.
word = "abc"
count = 0
for c in word:
    count = count + 1
print(count)
