"""audio — SDL2_mixer sound playback for hosted targets.

Provides simple WAV chunk and streaming music playback without requiring
any SDL2 knowledge.  SDL2_mixer must be installed alongside SDL2:

  Linux:   sudo apt install libsdl2-mixer-dev
  Windows: place SDL2_mixer.dll next to the executable (or in PATH)

Quick start::

    import audio
    audio.init()
    snd = audio.Sound("shoot.wav")
    mus = audio.Music("theme.ogg")
    mus.play()           # loops forever by default
    # in your game loop:
    snd.play()           # play on any free channel
    audio.quit()
"""
from __future__ import annotations

import _audio_sdl


# ---------------------------------------------------------------------------
# Device lifecycle
# ---------------------------------------------------------------------------

def init(freq: int = 44100, channels: int = 2, chunk: int = 2048) -> int:
    """Open the audio device.  Call once before loading sounds."""
    return _audio_sdl.open(freq, _audio_sdl.MIX_DEFAULT_FORMAT, channels, chunk)


def quit() -> int:
    """Close the audio device and free all mixer resources."""
    _audio_sdl.close()
    return 0


# ---------------------------------------------------------------------------
# Sound — a loaded WAV chunk
# ---------------------------------------------------------------------------

class Sound:
    """A loaded WAV sound effect; can be played simultaneously on any channel."""

    def __init__(self, path: str) -> int:
        self._chunk: int = _audio_sdl.load_wav(path)
        return 0

    def play(self, loops: int = 0) -> int:
        """Play the sound.  loops=0: once; loops=-1: forever."""
        return _audio_sdl.play(-1, self._chunk, loops)

    def play_on(self, channel: int, loops: int = 0) -> int:
        """Play on a specific channel (0-based)."""
        return _audio_sdl.play(channel, self._chunk, loops)

    def stop(self) -> int:
        """Halt all channels playing this chunk."""
        _audio_sdl.halt(-1)
        return 0

    def volume(self, vol: int) -> int:
        """Set playback volume (0–128)."""
        _audio_sdl.volume(-1, vol)
        return 0

    def playing(self, channel: int = -1) -> int:
        """Return non-zero if the given channel is currently playing."""
        return _audio_sdl.playing(channel)

    def free(self) -> int:
        """Release the chunk.  Do not use after calling this."""
        _audio_sdl.free(self._chunk)
        self._chunk = 0
        return 0


# ---------------------------------------------------------------------------
# Music — streaming music (OGG, MP3, MOD, FLAC, …)
# ---------------------------------------------------------------------------

class Music:
    """A streaming music track.  Only one music track plays at a time."""

    def __init__(self, path: str) -> int:
        self._mus: int = _audio_sdl.load_mus(path)
        return 0

    def play(self, loops: int = -1) -> int:
        """Play the track.  loops=-1: loop forever; 0: play once."""
        return _audio_sdl.play_mus(self._mus, loops)

    def stop(self) -> int:
        """Halt music playback."""
        _audio_sdl.halt_mus()
        return 0

    def volume(self, vol: int) -> int:
        """Set music volume (0–128)."""
        _audio_sdl.vol_mus(vol)
        return 0

    def playing(self) -> int:
        """Return non-zero if music is currently playing."""
        return _audio_sdl.playing_mus()

    def free(self) -> int:
        """Release the music resource.  Do not use after calling this."""
        _audio_sdl.free_mus(self._mus)
        self._mus = 0
        return 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_VOL: int = 128
DEFAULT_FREQ: int = 44100
DEFAULT_FORMAT: int = 0x8010
DEFAULT_CHANNELS: int = 2
