# probes: subprocess.run reports a nonzero exit code
# expect:
# 3
import subprocess
import sys

completed = subprocess.run([sys.executable, "-c", "raise SystemExit(3)"],
                           capture_output=True, text=True)
print(completed.returncode)
