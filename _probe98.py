# probe98: complex comprehensions with method calls, nested conditions
words = ["hello", "world", "Python", "is", "great", "fun"]

# Comprehension with method call
upper_long = [w.upper() for w in words if len(w) > 4]
print(len(upper_long))     # 4
print(upper_long[0])       # HELLO
print(upper_long[1])       # WORLD
print(upper_long[2])       # PYTHON

# Nested dict comprehension
word_lengths = {w: len(w) for w in words}
print(word_lengths["hello"])    # 5
print(word_lengths["is"])       # 2
print(word_lengths["Python"])   # 6

# Comprehension with multiple conditions
filtered = [w for w in words if len(w) > 2 if "o" in w]
print(len(filtered))     # 2 (hello, world)
print("hello" in filtered)   # True
print("world" in filtered)   # True

# Set comprehension
lengths = {len(w) for w in words}
print(2 in lengths)   # True (for "is")
print(5 in lengths)   # True (for "hello", "world", "great")
print(3 in lengths)   # True (for "fun")

# Comprehension producing tuples
indexed = [(i, w) for i, w in enumerate(words)]
print(indexed[0][0])   # 0
print(indexed[0][1])   # hello
print(indexed[5][1])   # fun
