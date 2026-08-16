# probes: argparse parses an explicit argument list
# expect:
# ada
# 3
import argparse

parser = argparse.ArgumentParser(prog="probe")
parser.add_argument("--count", type=int, default=1)
parser.add_argument("name")
args = parser.parse_args(["--count", "3", "ada"])
print(args.name)
print(args.count)
