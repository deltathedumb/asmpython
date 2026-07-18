# ext: interface
# expect-error: body must be exactly 'pass'

interface Shape:
    def area(self) -> int:
        return 1

print(1)
