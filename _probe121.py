# probe121: string formatting with dict/list repr in f-strings
# These test the repr of containers in format context
items = [1, 2, 3]
d = {"key": "val"}

# Container in f-string (uses repr)
print(f"list: {items}")   # list: [1, 2, 3]
print(f"dict: {d}")       # dict: {'key': 'val'}

# Nested containers
pairs = [(1, "a"), (2, "b"), (3, "c")]
print(f"pairs: {pairs}")   # pairs: [(1, 'a'), (2, 'b'), (3, 'c')]

# List of dicts
records = [{"name": "Alice", "age": 30}]
# Can't easily repr list of dicts without full repr support
# Just test length and access
print(len(records))         # 1
print(records[0]["name"])   # Alice

# Complex f-string with conditional
scores = [85, 92, 78, 95, 88]
avg = sum(scores) // len(scores)
print(f"avg={avg} grade={'A' if avg >= 90 else 'B' if avg >= 80 else 'C'}")
# avg=87 grade=B

# String multiplication in f-string
width = 20
title = "Results"
padding = (width - len(title)) // 2
print(f"{'=' * padding}{title}{'=' * padding}")
# ======Results======
