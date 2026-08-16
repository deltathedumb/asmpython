# probes: subprocess.run captures a child's stdout
# expect:
# 0
# from child
import subprocess
import sys

completed = subprocess.run([sys.executable, "-c", "print('from child')"],
                           capture_output=True, text=True)
print(completed.returncode)
print(completed.stdout.strip())
