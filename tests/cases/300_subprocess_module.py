# expect:
# -1
# -2
# -3
# 0
# 3
# 0
#
# caught

from __future__ import annotations
import subprocess
from subprocess import CalledProcessError, CompletedProcess

print(subprocess.PIPE)
print(subprocess.STDOUT)
print(subprocess.DEVNULL)

cp: CompletedProcess = subprocess.run("exit 0", shell=1)
print(cp.returncode)

rc: int = subprocess.call("exit 3")
print(rc)

result: tuple = subprocess.getstatusoutput("exit 0")
print(result[0])
print(result[1])

try:
    subprocess.check_call("exit 1")
except CalledProcessError:
    print("caught")
