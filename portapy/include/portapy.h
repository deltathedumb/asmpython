#ifndef PORTAPY_H
#define PORTAPY_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(PORTAPY_BUILD_DLL)
#    define PORTAPY_API __declspec(dllexport)
#  elif defined(PORTAPY_USE_DLL)
#    define PORTAPY_API __declspec(dllimport)
#  else
#    define PORTAPY_API
#  endif
#  define PORTAPY_CALL __cdecl
#else
#  define PORTAPY_API __attribute__((visibility("default")))
#  define PORTAPY_CALL
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define PORTAPY_ABI_VERSION 1u

typedef struct portapy_runtime portapy_runtime;
typedef struct portapy_value portapy_value;

typedef enum portapy_status {
    PORTAPY_OK = 0,
    PORTAPY_INVALID_ARGUMENT = 1,
    PORTAPY_COMPILE_ERROR = 2,
    PORTAPY_RUNTIME_ERROR = 3,
    PORTAPY_TYPE_ERROR = 4,
    PORTAPY_NOT_FOUND = 5,
    PORTAPY_CLOSED = 6,
    PORTAPY_INVALID_HANDLE = 7,
    PORTAPY_INTERRUPTED = 8,
    PORTAPY_ABI_MISMATCH = 9
} portapy_status;

typedef enum portapy_value_kind {
    PORTAPY_VALUE_NONE = 0,
    PORTAPY_VALUE_BOOL = 1,
    PORTAPY_VALUE_INT = 2,
    PORTAPY_VALUE_FLOAT = 3,
    PORTAPY_VALUE_STRING = 4,
    PORTAPY_VALUE_BYTES = 5,
    PORTAPY_VALUE_CALLABLE = 6,
    PORTAPY_VALUE_OBJECT = 7
} portapy_value_kind;

typedef struct portapy_bytes_view {
    const uint8_t *data;
    size_t size;
} portapy_bytes_view;

typedef void (PORTAPY_CALL *portapy_write_callback)(
    void *host_context,
    portapy_bytes_view bytes
);

typedef portapy_status (PORTAPY_CALL *portapy_read_callback)(
    void *host_context,
    portapy_bytes_view prompt,
    uint8_t *output,
    size_t output_capacity,
    size_t *output_size
);

typedef portapy_status (PORTAPY_CALL *portapy_import_callback)(
    void *host_context,
    portapy_bytes_view qualified_name,
    portapy_bytes_view *source_utf8
);

typedef struct portapy_config {
    size_t struct_size;
    uint32_t abi_version;
    uint32_t flags;
    void *host_context;
    portapy_write_callback stdout_write;
    portapy_write_callback stderr_write;
    portapy_read_callback stdin_read;
    portapy_import_callback import_source;
} portapy_config;

typedef struct portapy_error_info {
    portapy_status status;
    portapy_bytes_view type_name_utf8;
    portapy_bytes_view message_utf8;
    portapy_bytes_view traceback_utf8;
} portapy_error_info;

PORTAPY_API uint32_t PORTAPY_CALL portapy_abi_version(void);
PORTAPY_API const char *PORTAPY_CALL portapy_version_string(void);

PORTAPY_API portapy_status PORTAPY_CALL portapy_runtime_create(
    const portapy_config *config,
    portapy_runtime **out_runtime
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_runtime_destroy(
    portapy_runtime *runtime
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_runtime_interrupt(
    portapy_runtime *runtime
);

PORTAPY_API portapy_status PORTAPY_CALL portapy_exec_utf8(
    portapy_runtime *runtime,
    const uint8_t *source,
    size_t source_size,
    const uint8_t *filename,
    size_t filename_size
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_eval_utf8(
    portapy_runtime *runtime,
    const uint8_t *expression,
    size_t expression_size,
    const uint8_t *filename,
    size_t filename_size,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_get_global_utf8(
    portapy_runtime *runtime,
    const uint8_t *name,
    size_t name_size,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_call(
    portapy_runtime *runtime,
    portapy_value *callable,
    portapy_value *const *args,
    size_t arg_count,
    portapy_value **out_value
);

PORTAPY_API portapy_status PORTAPY_CALL portapy_value_retain(
    portapy_runtime *runtime,
    portapy_value *value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_release(
    portapy_runtime *runtime,
    portapy_value *value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_get_kind(
    portapy_runtime *runtime,
    portapy_value *value,
    portapy_value_kind *out_kind
);

PORTAPY_API portapy_status PORTAPY_CALL portapy_value_from_none(
    portapy_runtime *runtime,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_from_bool(
    portapy_runtime *runtime,
    int value,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_from_i64(
    portapy_runtime *runtime,
    int64_t value,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_from_f64(
    portapy_runtime *runtime,
    double value,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_from_utf8(
    portapy_runtime *runtime,
    const uint8_t *bytes,
    size_t size,
    portapy_value **out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_from_bytes(
    portapy_runtime *runtime,
    const uint8_t *bytes,
    size_t size,
    portapy_value **out_value
);

PORTAPY_API portapy_status PORTAPY_CALL portapy_value_as_bool(
    portapy_runtime *runtime,
    portapy_value *value,
    int *out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_as_i64(
    portapy_runtime *runtime,
    portapy_value *value,
    int64_t *out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_as_f64(
    portapy_runtime *runtime,
    portapy_value *value,
    double *out_value
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_as_utf8(
    portapy_runtime *runtime,
    portapy_value *value,
    portapy_bytes_view *out_view
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_value_as_bytes(
    portapy_runtime *runtime,
    portapy_value *value,
    portapy_bytes_view *out_view
);

PORTAPY_API portapy_status PORTAPY_CALL portapy_last_error(
    portapy_runtime *runtime,
    portapy_error_info *out_error
);
PORTAPY_API portapy_status PORTAPY_CALL portapy_clear_error(
    portapy_runtime *runtime
);

#ifdef __cplusplus
}
#endif

#endif
