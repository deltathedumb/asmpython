# probe20: str.format, str.join, str.split multi-result

# str.format
name = "world"
n = 42
print("hello {}".format(name))
print("x = {}".format(n))

# str.join
words = ["hello", "world", "foo"]
print(" ".join(words))
print(",".join(words))

# str.split
s = "a,b,c"
parts = s.split(",")
print(len(parts))
print(parts[0])
print(parts[2])

# str multiplication
print("ab" * 3)
print(3 * "x")
