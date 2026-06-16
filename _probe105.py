# probe105: **kwargs and dict unpacking
def greet(**kwargs) -> str:
    name = kwargs.get("name", "World")
    greeting = kwargs.get("greeting", "Hello")
    return greeting + ", " + name + "!"

print(greet(name="Alice", greeting="Hi"))   # Hi, Alice!
print(greet(name="Bob"))                     # Hello, Bob!
print(greet())                               # Hello, World!

# ** dict unpacking in call
def make_label(text: str, width: int, align: str) -> str:
    if align == "left":
        return text.ljust(width)
    elif align == "right":
        return text.rjust(width)
    return text.center(width)

opts = {"width": 10, "align": "right"}
# Can't do **opts in asmpython yet, so test manually
print(make_label("hi", opts["width"], opts["align"]))   # '        hi'

# Function that takes *args and **kwargs
def info(*args, **kwargs) -> None:
    print(len(args))
    for k in kwargs:
        print(k, kwargs[k])

info(1, 2, 3, name="test", val=42)
