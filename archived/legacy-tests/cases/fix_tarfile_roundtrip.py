# probes: tarfile adds and extracts a member
# expect:
# ['member.txt']
# inside
import os
import shutil
import tarfile
import tempfile

root = os.path.join(tempfile.gettempdir(), "asmpy_fix_tar")
archive_path = os.path.join(root, "bundle.tar")
member_path = os.path.join(root, "member.txt")
try:
    os.makedirs(root, exist_ok=True)
    with open(member_path, "w", encoding="utf-8") as handle:
        handle.write("inside")
    with tarfile.open(archive_path, "w") as archive:
        archive.add(member_path, arcname="member.txt")
    with tarfile.open(archive_path) as archive:
        print(archive.getnames())
        print(archive.extractfile("member.txt").read().decode("utf-8"))
finally:
    shutil.rmtree(root, ignore_errors=True)
