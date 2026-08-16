# expect:
# True
# False
# True
# True
# False

class Version:
    def __init__(self, major: int, minor: int) -> None:
        self.major: int = major
        self.minor: int = minor

    def __lt__(self, other: Version) -> int:
        if self.major != other.major:
            return 1 if self.major < other.major else 0
        return 1 if self.minor < other.minor else 0

    def __le__(self, other: Version) -> int:
        if self.major != other.major:
            return 1 if self.major < other.major else 0
        return 1 if self.minor <= other.minor else 0

    def __gt__(self, other: Version) -> int:
        return other.__lt__(self)

    def __ge__(self, other: Version) -> int:
        return other.__le__(self)

a = Version(1, 2)
b = Version(1, 3)
c = Version(2, 0)
print(a < b)
print(b < a)
print(a <= a)
print(c > a)
print(a >= c)
