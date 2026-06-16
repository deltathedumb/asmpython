# probe66: *args, **kwargs, default args, keyword-only args
def greet(name: str, greeting: str = "Hello") -> str:
    return greeting + ", " + name + "!"

print(greet("Alice"))            # Hello, Alice!
print(greet("Bob", "Hi"))        # Hi, Bob!
print(greet("Carol", greeting="Hey"))  # Hey, Carol!

# Multiple defaults
def info(name: str, age: int = 0, city: str = "Unknown") -> str:
    return name + " " + str(age) + " " + city

print(info("Dave", 30, "NYC"))   # Dave 30 NYC
print(info("Eve", city="LA"))    # Eve 0 LA
print(info("Frank"))             # Frank 0 Unknown

# *args
def sumall(*args: int) -> int:
    total: int = 0
    for v in args:
        total = total + v
    return total

print(sumall(1, 2, 3))       # 6
print(sumall(10, 20))        # 30
print(sumall())              # 0
