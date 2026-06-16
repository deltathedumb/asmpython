# probe12: str predicates, max/min, any/all, enumerate, dict.get, dict.keys/values

# 1. str predicates
print("123".isdigit())
print("abc".isalpha())
print("   ".isspace())
print("abc123".isalnum())
print("".isdigit())
print("ABC".isupper())
print("abc".islower())

# 2. max/min
nums = [3, 1, 4, 1, 5, 9, 2, 6]
print(max(nums))
print(min(nums))
print(max(1, 5, 3))
print(min(1, 5, 3))

# 3. any/all
flags = [True, True, False]
print(any(flags))
print(all(flags))
print(all([True, True, True]))
print(any([False, False]))

# 4. enumerate
xs = ["a", "b", "c"]
for i, v in enumerate(xs):
    print(i, v)

# 5. dict.get with default
d = {"x": 1, "y": 2}
print(d.get("x"))
print(d.get("z", 99))
print(d.get("z"))
