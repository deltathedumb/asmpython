# expect:
# 42
# -1
# -1
# 0
# caught_empty

def parse(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1

print(parse("42"))
print(parse("abc"))
print(parse("12x"))

# Leading/trailing whitespace is valid in Python's int()
print(parse("  0  "))

# Empty string also raises ValueError
try:
    n = int("")
except ValueError:
    print("caught_empty")
