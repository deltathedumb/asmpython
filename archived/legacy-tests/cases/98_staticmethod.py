# expect:
# 7
# HI!
# 30
# ab-ab
class Math:
    @staticmethod
    def add(a: int, b: int) -> int:
        return a + b
class StringUtils:
    @staticmethod
    def shout(s: str) -> str:
        return s.upper() + "!"
    @staticmethod
    def join2(a: str, b: str) -> str:
        return a + "-" + b
print(Math.add(3, 4))
print(StringUtils.shout("hi"))
print(Math.add(10, 20))
print(StringUtils.join2("ab", "ab"))
