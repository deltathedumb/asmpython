# expect:
# 16711680
# 65280
# 255
# 16777215
# 0
# 16711680
# 255
# 0
# 16777215
# 16711680
# 255
from lumen.framebuffer import rgb, bgr, BLACK, WHITE, RED, BLUE

print(rgb(255, 0, 0))
print(rgb(0, 255, 0))
print(rgb(0, 0, 255))
print(rgb(255, 255, 255))
print(rgb(0, 0, 0))
print(bgr(0, 0, 255))
print(bgr(255, 0, 0))
print(BLACK)
print(WHITE)
print(RED)
print(BLUE)
