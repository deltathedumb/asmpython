# expect:
# 10
# 6
# hello asmpython
import usermod_helpers

print(usermod_helpers.add(4, 6))
print(usermod_helpers.multiply(2, 3))
print(usermod_helpers.greet("asmpython"))
# note: usermod_helpers.CONSTANT attribute access is not yet supported;
# use `from usermod_helpers import CONSTANT` instead (see 100_user_import_from.py)
