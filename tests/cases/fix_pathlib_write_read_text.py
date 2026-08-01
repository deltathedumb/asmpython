# probes: Path.write_text / read_text round-trip
# expect:
# through pathlib
# True
import pathlib
import tempfile

path = pathlib.Path(tempfile.gettempdir()) / "asmpy_fix_pathlib.txt"
try:
    path.write_text("through pathlib", encoding="utf-8")
    print(path.read_text(encoding="utf-8"))
    print(path.exists())
finally:
    if path.exists():
        path.unlink()
