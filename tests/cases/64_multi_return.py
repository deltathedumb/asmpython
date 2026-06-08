# expect:
# q=3 r=2
# 7 1
# min=2 max=9
# both: 4 9

# A function that returns two values is a tuple under the hood; the call
# site unpacks it. This is the headline tuple use for self-hosting.
def divmod2(a, b):
    return a // b, a % b


q, r = divmod2(17, 5)
print(f"q={q} r={r}")


def split(lo, hi):
    return hi // lo, hi % lo


x, y = split(2, 15)
print(x, y)


# Multiple return statements with the same shape.
def minmax(a, b):
    if a < b:
        return a, b
    return b, a


lo, hi = minmax(9, 2)
print(f"min={lo} max={hi}")

# The returned tuple can also be kept whole and indexed.
pair = minmax(9, 4)
print("both:", pair[0], pair[1])
