"""lumen.gl — OpenGL context setup and GL constants for hosted (SDL2)
targets.

Part of `lumen`, asmpython's graphics/audio/input ecosystem. This module
does *not* wrap the GL function calls themselves -- `@<handle>.imported`
function pointers (the mechanism gl_import() uses; see the `gl_import()`
builtin and asmpython's CHANGELOG) resolve from `@handle.imported`-
decorated functions or methods declared in the compiled program itself.
`handle` (here, `glfns`) has to be a name resolvable where the decorator
is evaluated -- a module-level `glfns = gl_import()`, since Python
evaluates a class's decorators once at class-definition time, not per-
instance, so the bindings can live as top-level functions OR as methods
on a class (e.g. pugtk's GLRenderer3D wraps its own GL bindings as
methods, giving callers a plain `GLRenderer3D(window, camera)`
constructor with no GL boilerplate visible). This module supplies
everything around the bindings themselves: window/context setup and the
numeric GL constants those calls need (GL_TRIANGLES, GL_FLOAT,
GL_COMPILE_STATUS, ...).

Typical low-level setup (see pugtk.GLRenderer3D for the higher-level,
no-boilerplate path)::

    import lumen
    import lumen.gl as gl

    win = lumen.gl.create_gl_window("Hello GL", 800, 600)
    ctx = lumen.gl.create_context(win)

    glfns = gl_import()

    @glfns.imported
    def glClearColor(r: float, g: float, b: float, a: float) -> int:
        return 0

    # ... declare every other GL function your program calls the same way ...

    while True:
        glfns.glClearColor(0.1, 0.1, 0.15, 1.0)
        # ...
        lumen.gl.swap_window(win)
"""
from __future__ import annotations

import _gui_sdl


def create_gl_window(title: str, w: int, h: int, major: int = 3, minor: int = 3, depth_bits: int = 24) -> int:
    """Create an OpenGL-capable SDL2 window with a core-profile context
    attribute set (call create_context() next to actually create the GL
    context against this window). Returns the raw SDL_Window* handle."""
    _gui_sdl.init(_gui_sdl.INIT_VIDEO)
    _gui_sdl.set_gl_attribute(_gui_sdl.GL_CONTEXT_MAJOR_VERSION, major)
    _gui_sdl.set_gl_attribute(_gui_sdl.GL_CONTEXT_MINOR_VERSION, minor)
    _gui_sdl.set_gl_attribute(_gui_sdl.GL_CONTEXT_PROFILE_MASK, _gui_sdl.GL_CONTEXT_PROFILE_CORE)
    _gui_sdl.set_gl_attribute(_gui_sdl.GL_DOUBLEBUFFER, 1)
    _gui_sdl.set_gl_attribute(_gui_sdl.GL_DEPTH_SIZE, depth_bits)
    win: int = _gui_sdl.create_window(
        title,
        _gui_sdl.WINDOW_CENTERED, _gui_sdl.WINDOW_CENTERED,
        w, h,
        _gui_sdl.WINDOW_SHOWN | _gui_sdl.WINDOW_OPENGL,
    )
    return win


def create_context(win: int) -> int:
    """Create and make current a GL context for a window made with
    create_gl_window(). Must run before gl_import() resolves any function
    (SDL_GL_GetProcAddress only resolves against a current context)."""
    ctx: int = _gui_sdl.create_gl_context(win)
    _gui_sdl.make_gl_current(win, ctx)
    return ctx


def swap_window(win: int) -> int:
    """Present the back buffer (the frame everything was just drawn into)
    and pump SDL's event queue isn't included here -- call gui's own event
    handling separately if you need input."""
    return _gui_sdl.swap_gl_window(win)


def destroy(win: int, ctx: int) -> int:
    _gui_sdl.delete_gl_context(ctx)
    _gui_sdl.destroy_window(win)
    return 0


def shader_source(fn_ptr: int, shader: int, source: str) -> int:
    """Wraps glShaderSource(shader, 1, &source, NULL) -- glShaderSource's
    real signature (const GLchar *const *string) doesn't fit a plain
    int/float/str @gl.imported stub, so this goes through a small
    hand-written marshalling helper instead. `fn_ptr` is glShaderSource's
    own resolved function pointer: `int(getattr(glfns, "glShaderSource", 0))`
    after declaring a (never directly called) `@glfns.imported def
    glShaderSource() -> int: return 0` stub so getattr() type-checks."""
    return _gui_sdl.gl_shader_source_1(fn_ptr, shader, source)


# ---- Shader types -----------------------------------------------------------
VERTEX_SHADER:          int = 0x8B31
FRAGMENT_SHADER:        int = 0x8B30

# ---- Shader/program query params (glGetShaderiv/glGetProgramiv) -------------
COMPILE_STATUS:         int = 0x8B81
LINK_STATUS:            int = 0x8B82
INFO_LOG_LENGTH:        int = 0x8B84

# ---- Buffer targets -----------------------------------------------------------
ARRAY_BUFFER:           int = 0x8892
ELEMENT_ARRAY_BUFFER:   int = 0x8893

# ---- Buffer usage hints -------------------------------------------------------
STATIC_DRAW:            int = 0x88E4
DYNAMIC_DRAW:           int = 0x88E8
STREAM_DRAW:             int = 0x88E0

# ---- Vertex attribute element types -------------------------------------------
BYTE:                   int = 0x1400
UNSIGNED_BYTE:          int = 0x1401
SHORT:                  int = 0x1402
UNSIGNED_SHORT:         int = 0x1403
INT:                    int = 0x1404
UNSIGNED_INT:           int = 0x1405
FLOAT:                  int = 0x1406
DOUBLE:                 int = 0x140A

# ---- Primitive types (glDrawArrays/glDrawElements) -----------------------------
POINTS:                 int = 0x0000
LINES:                  int = 0x0001
LINE_LOOP:              int = 0x0002
LINE_STRIP:             int = 0x0003
TRIANGLES:              int = 0x0004
TRIANGLE_STRIP:         int = 0x0005
TRIANGLE_FAN:           int = 0x0006

# ---- Capabilities (glEnable/glDisable) -----------------------------------------
DEPTH_TEST:             int = 0x0B71
CULL_FACE:              int = 0x0B44
BLEND:                  int = 0x0BE2

# ---- Depth comparison functions (glDepthFunc) ------------------------------------
NEVER:                  int = 0x0200
LESS:                   int = 0x0201
EQUAL:                  int = 0x0202
LEQUAL:                 int = 0x0203
GREATER:                int = 0x0204
NOTEQUAL:               int = 0x0205
GEQUAL:                 int = 0x0206
ALWAYS:                 int = 0x0207

# ---- Face culling (glCullFace/glFrontFace) -------------------------------------
FRONT:                  int = 0x0404
BACK:                   int = 0x0405
FRONT_AND_BACK:         int = 0x0408
CW:                     int = 0x0900
CCW:                    int = 0x0901

# ---- Clear mask bits (glClear) --------------------------------------------------
COLOR_BUFFER_BIT:       int = 0x4000
DEPTH_BUFFER_BIT:       int = 0x0100
STENCIL_BUFFER_BIT:     int = 0x0400

# ---- Misc enums -------------------------------------------------------------
FALSE:                  int = 0
TRUE:                   int = 1
NO_ERROR:               int = 0

# ---- glGetString names --------------------------------------------------------
VENDOR:                 int = 0x1F00
RENDERER:               int = 0x1F01
VERSION:                int = 0x1F02
SHADING_LANGUAGE_VERSION: int = 0x8B8C
