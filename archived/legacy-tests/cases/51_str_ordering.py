# expect:
# 1
# 0
# 1
# 0
# 1
# 1
# 1
# 0

# strcmp("apple", "banana") < 0 -> "apple" < "banana" is true
print(int("apple" < "banana"))
print(int("banana" < "apple"))
print(int("apple" <= "apple"))
print(int("apple" < "apple"))

# > and >= mirror the above.
print(int("banana" > "apple"))
print(int("banana" >= "banana"))

# Mixed with variables.
a = "alpha"
b = "beta"
print(int(a < b))
print(int(a > b))
