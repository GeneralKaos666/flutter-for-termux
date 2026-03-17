/*
 * Stub header for Termux/cross-compilation builds.
 *
 * hardware/hwvulkan.h is an Android platform-internal HAL header not included
 * in the public NDK.  SwiftShader includes it (inside #ifdef __ANDROID__) to
 * register itself as an Android Vulkan HAL module.  Because we cross-compile
 * with --target=aarch64-linux-androidXX the preprocessor defines __ANDROID__,
 * so this file is needed for compilation to succeed on a Termux/Linux host
 * where the real AOSP HAL headers are unavailable.
 *
 * Based on AOSP hardware/libhardware/include/hardware/hwvulkan.h and the
 * minimal types from hardware/libhardware/include/hardware/hardware.h that
 * SwiftShader's VkGetProcAddress.cpp actually uses.
 */

#ifndef ANDROID_HWVULKAN_H
#define ANDROID_HWVULKAN_H

#include <stdint.h>
#include <vulkan/vulkan.h>

/* ----------------------------------------------------------------
 * Minimal subset of hardware/hardware.h
 * ---------------------------------------------------------------- */

#define MAKE_HW_TAG(a, b, c, d)         \
    (((uint32_t)(unsigned char)(a) << 24) | \
     ((uint32_t)(unsigned char)(b) << 16) | \
     ((uint32_t)(unsigned char)(c) <<  8) | \
      (uint32_t)(unsigned char)(d))

#ifndef HARDWARE_MODULE_TAG
#define HARDWARE_MODULE_TAG MAKE_HW_TAG('H', 'W', 'M', 'T')
#endif

#ifndef HARDWARE_DEVICE_TAG
#define HARDWARE_DEVICE_TAG MAKE_HW_TAG('H', 'W', 'D', 'T')
#endif

#ifndef HARDWARE_HAL_API_VERSION
#define HARDWARE_HAL_API_VERSION 0
#endif

#ifndef HARDWARE_MODULE_API_VERSION
#define HARDWARE_MODULE_API_VERSION(maj, min) \
    ((((uint32_t)(maj) & 0xFF) << 8) | ((uint32_t)(min) & 0xFF))
#endif

#ifndef HARDWARE_DEVICE_API_VERSION
#define HARDWARE_DEVICE_API_VERSION(maj, min) \
    ((((uint32_t)(maj) & 0xFF) << 8) | ((uint32_t)(min) & 0xFF))
#endif

typedef struct hw_module_t hw_module_t;
typedef struct hw_module_methods_t hw_module_methods_t;
typedef struct hw_device_t hw_device_t;

struct hw_module_methods_t {
    int (*open)(const hw_module_t *module, const char *id,
                hw_device_t **device);
};

struct hw_module_t {
    uint32_t            tag;
    uint16_t            module_api_version;
    uint16_t            hal_api_version;
    const char         *id;
    const char         *name;
    const char         *author;
    hw_module_methods_t *methods;
    void               *dso;
    uint32_t            reserved[32 - 7];
};

struct hw_device_t {
    uint32_t     tag;
    uint32_t     version;
    hw_module_t *module;
    uint32_t     reserved[12];
    int        (*close)(hw_device_t *device);
};

/* ----------------------------------------------------------------
 * hwvulkan.h definitions
 * ---------------------------------------------------------------- */

#define HWVULKAN_HARDWARE_MODULE_ID     "vulkan"
#define HWVULKAN_DEVICE_0               "vk0"

#define HWVULKAN_MODULE_API_VERSION_0_1 HARDWARE_MODULE_API_VERSION(0, 1)
#define HWVULKAN_DEVICE_API_VERSION_0_1 HARDWARE_DEVICE_API_VERSION(0, 1)

typedef struct hwvulkan_module_t {
    hw_module_t common;
} hwvulkan_module_t;

typedef struct hwvulkan_device_t {
    hw_device_t                               common;
    PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
    PFN_vkCreateInstance                       CreateInstance;
    PFN_vkGetInstanceProcAddr                  GetInstanceProcAddr;
} hwvulkan_device_t;

#endif /* ANDROID_HWVULKAN_H */
