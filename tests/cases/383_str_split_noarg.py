# expect:
# 3
# hello
# world
# foo
# 1
# hello
# 0

s: str = "hello world foo"
parts = s.split()
print(len(parts))
print(parts[0])
print(parts[1])
print(parts[2])

# single word
w: str = "hello"
ws = w.split()
print(len(ws))
print(ws[0])

# empty string
e: str = ""
es = e.split()
print(len(es))
