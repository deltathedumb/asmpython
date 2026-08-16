# expect:
# True False
class Version:
    def __init__(self, major, minor):
        self.major = major
        self.minor = minor
    def __lt__(self, o):
        return (self.major, self.minor) < (o.major, o.minor)
print(Version(1, 2) < Version(1, 5), Version(2, 0) < Version(1, 9))
# asmpython (beta/3.14.0) MISMATCH: prints 'True True\n' (wrong).
