# probe: various patterns

# 1. str.join
words = ["hello", "world"]
result = " ".join(words)
print(result)

# 2. str.startswith / endswith
s = "hello"
print(s.startswith("hel"))
print(s.endswith("lo"))

# 3. int.to_bytes / from_bytes (probe failure)
# n = 42
# b = n.to_bytes(4, "little")

# 4. str.count
s2 = "banana"
print(s2.count("a"))

# 5. list.count
xs = [1, 2, 1, 3, 1]
print(xs.count(1))
