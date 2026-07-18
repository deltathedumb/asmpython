# ext: immutable
# expect-error: is immutable outside

class Config:
    @immutable
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

c = Config("prod")
c.name = "dev"
print(c.name)
