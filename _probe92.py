# probe92: set operations (str sets)
a = {"apple", "banana", "cherry"}
b = {"banana", "date", "cherry"}

# set operations
union = a | b
inter = a & b
diff = a - b
sym = a ^ b

print(len(union))   # 4 (apple, banana, cherry, date)
print(len(inter))   # 2 (banana, cherry)
print(len(diff))    # 1 (apple)
print(len(sym))     # 2 (apple, date)

# in/not in
print("apple" in a)       # True
print("apple" in b)       # False
print("apple" not in b)   # True

# set.add, discard, remove
s = {"x", "y", "z"}
s.add("w")
print(len(s))   # 4
s.discard("x")
print(len(s))   # 3
print("x" in s)  # False

# set from list (dedup)
lst = ["a", "b", "a", "c", "b"]
unique = set(lst)
print(len(unique))   # 3

# Iteration
words = {"hello", "world"}
found_hello = False
for w in words:
    if w == "hello":
        found_hello = True
print(found_hello)   # True
