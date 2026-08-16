# expect:
# 0
# 12
# -1
# 12
# 6

s: str = "hello world hello"
print(s.find("hello"))
print(s.find("hello", 5))
print(s.find("xyz", 0))
print(s.rfind("hello"))
print(s.find("world"))
