# tier: spec
# ref: library/stdtypes.html#str.startswith
# expect:
# True True
# True
# True
# True
# True
s = "filename.txt"
print(s.startswith("file"), s.endswith(".txt"))
print(s.startswith(("a", "file")))
print(s.endswith((".py", ".txt")))
print(s.startswith("name", 4))
print(s.endswith("file", 0, 4))
