# expect:
# 10
# 20
# 30
# 40
# 50
# test

import logging

print(logging.DEBUG)
print(logging.INFO)
print(logging.WARNING)
print(logging.ERROR)
print(logging.CRITICAL)

log = logging.getLogger("test")
print(log.name)
