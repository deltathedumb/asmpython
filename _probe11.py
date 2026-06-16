# probe: more patterns

# 1. str.find() vs str.index()
s = "hello world"
print(s.find("world"))
print(s.find("xyz"))  # -1 if not found
print(s.index("world"))

# 2. str.isdigit / isalpha / isspace
print("123".isdigit())
print("abc".isalpha())
print("   ".isspace())
print("abc123".isalnum())

# 3. max/min on list
nums = [3, 1, 4, 1, 5, 9, 2, 6]
print(max(nums))
print(min(nums))

# 4. sum with start
print(sum([1, 2, 3]))
print(sum([1, 2, 3], 10))

# 5. any/all
flags = [True, True, False]
print(any(flags))
print(all(flags))
print(all([True, True, True]))
