# probe53: type conversions and type checking
# type() checks
x = 42
print(type(x))       # <class 'int'>? or "int"?

# isinstance checks
print(isinstance(42, int))         # True
print(isinstance("hi", str))       # True
print(isinstance(3.14, float))     # True
print(isinstance(42, str))         # False

# Conversions with validation
s = "123"
n = int(s)
print(n + 1)   # 124

s2 = str(42)
print(s2 + "!")  # 42!

# Float conversions
f = float("3.14")
print(f > 3.0)   # True

# int from float
i = int(3.7)
print(i)   # 3

# bool conversions
print(bool(0))   # False
print(bool(1))   # True
print(bool(""))  # False?
print(bool("x")) # True?
