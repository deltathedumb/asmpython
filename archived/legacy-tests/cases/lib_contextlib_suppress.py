# expect:
# survived
from contextlib import suppress
with suppress(ValueError):
    raise ValueError('x')
print('survived')
# asmpython (beta/3.14.0) rejects at compile: [E021] suppress() takes 0 argument(s), got 1
