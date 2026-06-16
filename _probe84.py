# probe84: type conversions and edge cases
# int to str conversions
print(str(0))       # 0
print(str(-1))      # -1
print(str(1000000)) # 1000000

# str to int
print(int("42"))     # 42
print(int("-99"))    # -99
print(int("0"))      # 0

# bool operations
print(True and False)   # False
print(True or False)    # True
print(not True)         # False
print(not False)        # True
print(bool(0))          # False
print(bool(1))          # True
print(bool(""))         # False
print(bool("x"))        # True
print(bool([]))         # False
print(bool([1]))        # True

# int(bool)
print(int(True))    # 1
print(int(False))   # 0

# Truthiness in conditions
if 0:
    print("no")
else:
    print("zero is falsy")   # zero is falsy

if "":
    print("no")
else:
    print("empty str is falsy")  # empty str is falsy

if []:
    print("no")
else:
    print("empty list is falsy")  # empty list is falsy
