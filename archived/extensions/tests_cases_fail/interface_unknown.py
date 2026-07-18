# ext: interface
# expect-error: no such interface was declared

class Circle(interface=DoesNotExist):
    def area(self) -> int:
        return 1

print(Circle().area())
