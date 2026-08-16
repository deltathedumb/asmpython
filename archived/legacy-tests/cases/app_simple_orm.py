# expect:
# 2 a
class Record:
    def __init__(self, **fields):
        self.fields = fields
    def get(self, key):
        return self.fields.get(key)
records = [Record(name='a', age=30), Record(name='b', age=25)]
adults = [r for r in records if r.get('age') >= 18]
print(len(adults), records[0].get('name'))
# asmpython (beta/3.14.0) MISMATCH: prints '2 5368746006\n' (wrong).
