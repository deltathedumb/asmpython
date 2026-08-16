# expect:
# True
# True
# False
# True
# True

words = ["apple", "banana", "apple", "cherry"]
unique = {w for w in words}
print("apple" in unique)
print("banana" in unique)
print("grape" in unique)

# frozenset from comprehension also works
fs = frozenset(w for w in ["x", "y", "x"])
print("x" in fs)
print("y" in fs)
