# expect:
# result.txt
# 1

import argparse

ap = argparse.ArgumentParser(description='test')
ap.add_argument('--output', default='out.txt')
ap.add_argument('--verbose', action='store_true')
args = ['--output', 'result.txt', '--verbose']
ns = ap.parse_args(args)
print(ns.get('output'))
print(ns.get_flag('verbose'))
