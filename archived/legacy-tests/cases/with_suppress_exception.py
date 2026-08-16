# expect:
# survived
class Suppressor:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, *a):
        return exc_type is ValueError
with Suppressor():
    raise ValueError('suppressed')
print('survived')
# asmpython (beta/3.14.0) runtime failure: exit 0x1
