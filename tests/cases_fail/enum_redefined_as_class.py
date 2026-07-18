# ext: enum
# expect-error: collides with existing name

enum Color:
    RED
    GREEN

class Color:
    def __init__(self) -> None:
        self.x = 1

print(Color.RED)
