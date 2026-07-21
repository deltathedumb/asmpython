# expect:
# 1

import sys


def selected_path(platform=None):
    current = platform or sys.platform
    return current.startswith("linux")


print(selected_path())
