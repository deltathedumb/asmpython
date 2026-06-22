# Vendored SDL2

Binaries used by the `lumen` stdlib package (`import lumen`,
`import lumen.audio`) so GUI/audio programs build and run without the
user installing any system SDL2 packages.

## windows/

| File                  | Source                                                       | Notes |
|------------------------|---------------------------------------------------------------|-------|
| `SDL2.dll`, `libSDL2.dll.a` | [SDL 2.30.9](https://github.com/libsdl-org/SDL/releases/tag/release-2.30.9) official `mingw` devel release, `x86_64-w64-mingw32/` subtree | unmodified |
| `SDL2_mixer.dll`, `libSDL2_mixer.dll.a` | [SDL_mixer 2.8.0](https://github.com/libsdl-org/SDL_mixer/releases/tag/release-2.8.0) official `mingw` devel release, `x86_64-w64-mingw32/` subtree | unmodified |
| `libSDL2_ttf.dll`, `libSDL2_ttf.dll.a` | Built from [SDL_ttf 2.22.0](https://github.com/libsdl-org/SDL_ttf/releases/tag/release-2.22.0) source | custom minimal build, see below |
| `libfreetype.dll` | Built from [FreeType 2.13.3](https://github.com/freetype/freetype/releases/tag/VER-2-13-3) source | custom minimal build, see below |

The official SDL_ttf mingw devel release statically bundles HarfBuzz
and FreeType and is ~68MB — too large to vendor. Instead, `libSDL2_ttf.dll`
here was built directly from SDL_ttf's source against a minimal FreeType
(no HarfBuzz, no Brotli/bzip2/PNG, no zlib), bringing the pair down to
under 1MB combined. This drops HarfBuzz-dependent complex text shaping
(e.g. Arabic/Indic scripts) but keeps standard TTF/OTF glyph rendering,
which is all `lumen.Font`/`Canvas.draw_ttf` use.

Build commands used (run from a `w64devkit`-style mingw cross-compile
environment with `cmake`/`x86_64-w64-mingw32-gcc` on PATH):

```sh
# FreeType (minimal, shared)
cmake -G "MinGW Makefiles" \
  -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc -DCMAKE_SYSTEM_NAME=Windows \
  -DCMAKE_BUILD_TYPE=MinSizeRel -DBUILD_SHARED_LIBS=ON \
  -DFT_DISABLE_HARFBUZZ=ON -DFT_DISABLE_BROTLI=ON \
  -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_PNG=ON -DFT_DISABLE_ZLIB=OFF \
  <freetype-source>
mingw32-make

# SDL2_ttf (direct gcc compile, skips the project's CMake/HarfBuzz auto-detect)
x86_64-w64-mingw32-gcc -shared -O2 -DDLL_EXPORT \
  -I<sdl_ttf-source> -I<sdl_ttf-source>/external \
  -I<sdl2-devel>/include/SDL2 -I<freetype-source>/include \
  <sdl_ttf-source>/SDL_ttf.c \
  -L<sdl2-devel>/lib -L<freetype-build> \
  -lSDL2 -lfreetype \
  -o libSDL2_ttf.dll -Wl,--out-implib,libSDL2_ttf.dll.a -Wl,--enable-auto-import
```

Verified by compiling a standalone C program against these exact
binaries: `TTF_Init`/`TTF_OpenFont` (real Arial.ttf)/`TTF_FontHeight`/
`TTF_RenderText_Blended` all returned correct, sane values.

## Licenses

SDL2/SDL2_mixer/SDL2_ttf are zlib-licensed; FreeType is FreeType-licensed
(BSD-style) or GPLv2, dual-licensed (see `FTL.TXT`). See the per-project
`LICENSE-*.txt` files in this directory.

## Linux

Not vendored — `apt install libsdl2-dev libsdl2-ttf-dev libsdl2-mixer-dev`
(or the equivalent for your distro) is a single command on every major
Linux package manager, unlike Windows which has no universal equivalent.
`lumen`'s Linux target still links against the system SDL2 the normal way.
