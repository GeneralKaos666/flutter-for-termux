#ifndef ANDROID_INCLUDE_HARDWARE_HARDWARE_H
#define ANDROID_INCLUDE_HARDWARE_HARDWARE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MAKE_TAG_CONSTANT(A, B, C, D) (((A) << 24) | ((B) << 16) | ((C) << 8) | (D))

#define HARDWARE_MODULE_TAG MAKE_TAG_CONSTANT('H', 'W', 'M', 'T')
#define HARDWARE_DEVICE_TAG MAKE_TAG_CONSTANT('H', 'W', 'D', 'T')

#define HARDWARE_MODULE_API_VERSION(maj, min) (((maj) << 8) | (min))
#define HARDWARE_DEVICE_API_VERSION_2(maj, min, sub) (((maj) << 24) | ((min) << 16) | (sub))

/* Simplified HAL API version used by SwiftShader HAL stubs. */
#define HARDWARE_HAL_API_VERSION HARDWARE_MODULE_API_VERSION(1, 0)

struct hw_module_t;
struct hw_module_methods_t;
struct hw_device_t;

typedef struct hw_module_t {
    uint32_t tag;
    uint16_t version_major;
    uint16_t version_minor;
    const char* id;
    const char* name;
    const char* author;
    struct hw_module_methods_t* methods;
    void* dso;
#ifdef __LP64__
    uint64_t reserved[32 - 7];
#else
    uint32_t reserved[32 - 7];
#endif
} hw_module_t;

typedef struct hw_module_methods_t {
    int (*open)(const struct hw_module_t* module, const char* id,
                struct hw_device_t** device);
} hw_module_methods_t;

typedef struct hw_device_t {
    uint32_t tag;
    uint32_t version;
    struct hw_module_t* module;
#ifdef __LP64__
    uint64_t reserved[12];
#else
    uint32_t reserved[12];
#endif
    int (*close)(struct hw_device_t* device);
} hw_device_t;

#define HAL_MODULE_INFO_SYM HMI
#define HAL_MODULE_INFO_SYM_AS_STR "HMI"

#ifdef __cplusplus
}
#endif

#endif /* ANDROID_INCLUDE_HARDWARE_HARDWARE_H */
