# set comp with string elements
words = ["apple", "banana", "apple", "cherry"]
unique = {w for w in words}
print("apple" in unique)   # True
print("banana" in unique)  # True
print("grape" in unique)   # False
