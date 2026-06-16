# probe119: runtime error handling - IndexError, KeyError, ZeroDivisionError
# IndexError
xs = [1, 2, 3]
try:
    v = xs[10]
    print("no error")
except IndexError as e:
    print("IndexError caught")

# KeyError
d = {"a": 1}
try:
    v = d["z"]
    print("no error")
except KeyError as e:
    print("KeyError caught")
    print(str(e))   # 'z'

# ZeroDivisionError
try:
    v = 10 // 0
except ZeroDivisionError:
    print("ZeroDivisionError caught")

# ValueError from int()
try:
    v = int("not a number")
except ValueError as e:
    print("ValueError caught")

# Catching multiple
def risky(x: int) -> int:
    if x == 0:
        return 1 // 0
    if x < 0:
        return int("bad")
    return x

for val in [1, 0, -1]:
    try:
        print(risky(val))
    except ZeroDivisionError:
        print("zero div at", val)
    except ValueError:
        print("value err at", val)
