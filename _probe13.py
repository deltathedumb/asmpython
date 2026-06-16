# probe13: str.strip/replace/startswith/endswith
s = "  hello  "
print(s.strip())
print(s.lstrip())
print(s.rstrip())

t = "hello world"
print(t.replace("world", "there"))
print(t.startswith("hello"))
print(t.endswith("world"))
print(t.startswith("world"))
