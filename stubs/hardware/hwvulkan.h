#ifndef ANDROID_HWVULKAN_H
#define ANDROID_HWVULKAN_H

#include <hardware/hardware.h>
#include <vulkan/vulkan.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HWVULKAN_HARDWARE_MODULE_ID "vulkan"

#define HWVULKAN_MODULE_API_VERSION_0_1 HARDWARE_MODULE_API_VERSION(0, 1)
#define HWVULKAN_DEVICE_API_VERSION_0_1 HARDWARE_DEVICE_API_VERSION_2(0, 1, 0)

#define HWVULKAN_DEVICE_0 "vk0"

typedef struct hwvulkan_module_t {
    struct hw_module_t common;
} hwvulkan_module_t;

typedef union {
    uintptr_t magic;
    const void* vtbl;
} hwvulkan_dispatch_t;

typedef struct hwvulkan_device_t {
    struct hw_device_t common;
    PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
    PFN_vkCreateInstance CreateInstance;
    PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
} hwvulkan_device_t;

#ifdef __cplusplus
}
#endif

#endif /* ANDROID_HWVULKAN_H */
