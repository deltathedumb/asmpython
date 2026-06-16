# probe127: re module patterns
import re

# Basic match
m = re.match(r"\d+", "123abc")
print(m is not None)   # True
print(m.group())       # 123

# No match
m2 = re.match(r"\d+", "abc")
print(m2 is None)      # True

# Search
m3 = re.search(r"\d+", "abc123def")
print(m3 is not None)  # True
print(m3.group())      # 123

# findall
nums = re.findall(r"\d+", "a1b22c333d")
print(len(nums))   # 3
print(nums[0])     # 1
print(nums[2])     # 333

# sub
result = re.sub(r"\d+", "X", "a1b22c333")
print(result)   # aXbXcX

# Groups
m4 = re.match(r"(\w+)@(\w+)\.(\w+)", "user@example.com")
if m4:
    print(m4.group(1))   # user
    print(m4.group(2))   # example
    print(m4.group(3))   # com

# split
parts = re.split(r"\s+", "hello   world  foo")
print(len(parts))   # 3
print(parts[0])     # hello
print(parts[2])     # foo
