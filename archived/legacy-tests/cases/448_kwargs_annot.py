# expect:
# Hello, Alice!
# Hi, Bob!

def greet(name: str, **kwargs: str) -> str:
    greeting = kwargs.get("greeting", "Hello")
    return f"{greeting}, {name}!"

print(greet("Alice"))
print(greet("Bob", greeting="Hi"))
