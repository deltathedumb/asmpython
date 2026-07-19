#include "portapy.h"

#include <stdio.h>

int main(void) {
    portapy_config config = {0};
    portapy_runtime *runtime = NULL;
    portapy_value *value = NULL;
    int64_t answer = 0;

    config.struct_size = sizeof(config);
    config.abi_version = PORTAPY_ABI_VERSION;

    if (portapy_runtime_create(&config, &runtime) != PORTAPY_OK) {
        return 1;
    }
    if (portapy_eval_utf8(
            runtime,
            (const uint8_t *)"6 * 7",
            5,
            (const uint8_t *)"<embed>",
            7,
            &value) != PORTAPY_OK) {
        portapy_runtime_destroy(runtime);
        return 2;
    }
    if (portapy_value_as_i64(runtime, value, &answer) != PORTAPY_OK) {
        portapy_value_release(runtime, value);
        portapy_runtime_destroy(runtime);
        return 3;
    }

    printf("%lld\n", (long long)answer);
    portapy_value_release(runtime, value);
    portapy_runtime_destroy(runtime);
    return answer == 42 ? 0 : 4;
}
