# ext: overload
# expect:
# int:5
# str:hi

class Formatter:
    @overload
    def format(self, x: int) -> str:
        return "int:" + str(x)

    @overload
    def format(self, x: str) -> str:
        return "str:" + x

f = Formatter()
print(f.format(5))
print(f.format("hi"))
