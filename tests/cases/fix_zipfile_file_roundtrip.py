# probes: zipfile writes and re-reads an archive on disk
# expect:
# ['member.txt']
# inside
import os
import tempfile
import zipfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_zip.zip")
try:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("member.txt", "inside")
    with zipfile.ZipFile(path) as archive:
        print(archive.namelist())
        print(archive.read("member.txt").decode("utf-8"))
finally:
    if os.path.exists(path):
        os.remove(path)
