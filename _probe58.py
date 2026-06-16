# probe58: typing features, optional, union types
from typing import Optional

def find_item(items: list[str], target: str) -> Optional[str]:
    for item in items:
        if item == target:
            return item
    return None

result = find_item(["a", "b", "c"], "b")
if result is not None:
    print("found:", result)  # found: b
else:
    print("not found")

result2 = find_item(["a", "b", "c"], "z")
if result2 is None:
    print("not found")  # not found

# Optional chaining with default
def greet(name: Optional[str] = None) -> str:
    if name is None:
        return "Hello, World!"
    return f"Hello, {name}!"

print(greet())         # Hello, World!
print(greet("Alice"))  # Hello, Alice!
