# probes: dataclass(order=True) derives comparisons
# expect:
# True
# 1
import dataclasses


@dataclasses.dataclass(order=True)
class Version:
    major: int
    minor: int


print(Version(1, 2) < Version(1, 3))
print(sorted([Version(2, 0), Version(1, 5)])[0].major)
