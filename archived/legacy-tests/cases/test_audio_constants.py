# expect:
# 32784
# 44100
# 2
# 128
import _audio_sdl

print(_audio_sdl.MIX_DEFAULT_FORMAT)
print(_audio_sdl.MIX_DEFAULT_FREQ)
print(_audio_sdl.MIX_DEFAULT_CHANNELS)
print(_audio_sdl.MIX_MAX_VOLUME)
