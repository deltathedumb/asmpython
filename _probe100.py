# probe100: string interpolation with complex types, list/dict repr
# list repr in print
xs = [1, 2, 3, 4, 5]
print(xs)   # [1, 2, 3, 4, 5]

# nested list repr
matrix = [[1, 2], [3, 4], [5, 6]]
print(matrix)   # [[1, 2], [3, 4], [5, 6]]

# tuple repr
t = (1, "hello", 3)
print(t)   # (1, hello, 3) or (1, 'hello', 3) ?

# dict repr in f-string
d = {"a": 1, "b": 2}
# Note: dict repr ordering may vary

# list in f-string
print(f"xs = {xs}")   # xs = [1, 2, 3, 4, 5]

# None repr
x = None
print(x)       # None
print(str(x))  # None

# bool repr
print(True)    # True
print(False)   # False

# list of strings repr
words = ["hello", "world"]
print(words)   # ['hello', 'world'] or [hello, world]?

# empty containers
print([])    # []
print(())    # ()
