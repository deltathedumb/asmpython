# expect:
# True
# False
# True
# False
# True
# False
# True
# False
# True
# False
# True
# False

# Character-class predicates. Empty string is False for all of them; the cased
# predicates require at least one cased char.

print("123".isdigit())     # 1
print("12a".isdigit())     # 0
print("abc".isalpha())     # 1
print("ab1".isalpha())     # 0
print("a1b2".isalnum())    # 1
print("a 1".isalnum())     # 0  (space is not alnum)
print("  \t\n".isspace())  # 1
print(" x ".isspace())     # 0
print("HELLO".isupper())   # 1
print("Hello".isupper())   # 0
print("world".islower())   # 1
print("World".islower())   # 0
