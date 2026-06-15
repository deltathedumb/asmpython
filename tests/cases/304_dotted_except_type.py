# expect:
# caught
# 1
# ok
# tuple-caught
# boom

from __future__ import annotations
import subprocess

try:
    subprocess.check_call("exit 1")
except subprocess.CalledProcessError as e:
    print("caught")
    print(e)

try:
    subprocess.check_call("exit 0")
except subprocess.CalledProcessError:
    print("should not happen")
else:
    print("ok")

try:
    raise ValueError("boom")
except (subprocess.CalledProcessError, ValueError) as e:
    print("tuple-caught")
    print(e)
