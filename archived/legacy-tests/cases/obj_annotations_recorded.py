# probes: __annotations__ records declared types
# expect:
# ['count', 'name']
class Record:
    name: str
    count: int


print(sorted(Record.__annotations__.keys()))
