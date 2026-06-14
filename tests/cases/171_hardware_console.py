# expect:
# [2J[H0
# 0
# ab2
#
# 1
# 0
# [4;8H3
# 7
# [31m[40mX

# asmlib.hardware has no CPython equivalent (see 169_hardware_real_ops.py);
# this checks the high-level console_* API: text output, cursor tracking,
# and the color/clear/cursor-positioning escapes on the hosted target.
from asmlib.hardware import console_clear, console_putc, console_write, console_set_color, console_set_cursor, console_get_row, console_get_col

console_clear()
print(console_get_row())
print(console_get_col())

console_write("ab")
print(console_get_col())

console_putc(10)
print(console_get_row())
print(console_get_col())

console_set_cursor(3, 7)
print(console_get_row())
print(console_get_col())

console_set_color(1, 0)
console_write("X")
print("")
