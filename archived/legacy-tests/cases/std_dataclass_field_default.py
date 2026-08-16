# probes: a dataclass applies declared defaults
# expect:
# x
# 3
import dataclasses


@dataclasses.dataclass
class Config:
    name: str
    retries: int = 3


c = Config("x")
print(c.name)
print(c.retries)
