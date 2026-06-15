# expect:
# tests/runner.py
# hello.txt
# .txt
# hello
# /tmp/hello.txt

from pathlib import Path

p2 = Path('tests') / 'runner.py'
print(str(p2))
p3 = Path('/tmp/hello.txt')
print(p3.name())
print(p3.suffix())
print(p3.stem())
p4 = p3.parent()
print(str(p4) + '/hello.txt')
