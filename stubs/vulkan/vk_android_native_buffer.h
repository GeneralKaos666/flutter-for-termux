#ifndef VULKAN_VK_ANDROID_NATIVE_BUFFER_H_
#define VULKAN_VK_ANDROID_NATIVE_BUFFER_H_ 1

#include <stdint.h>
#include <vulkan/vulkan.h>

#ifdef __cplusplus
extern "C" {
#endif

struct native_handle;
struct AHardwareBuffer;

typedef const struct native_handle* buffer_handle_t;

#define VK_ANDROID_native_buffer 1
#define VK_ANDROID_NATIVE_BUFFER_EXTENSION_NUMBER 11
#define VK_ANDROID_NATIVE_BUFFER_SPEC_VERSION 11
#define VK_ANDROID_NATIVE_BUFFER_EXTENSION_NAME "VK_ANDROID_native_buffer"

#define VK_ANDROID_NATIVE_BUFFER_ENUM(type, id) \
    ((type)(1000000000 + (1000 * (VK_ANDROID_NATIVE_BUFFER_EXTENSION_NUMBER - 1)) + (id)))

#define VK_STRUCTURE_TYPE_NATIVE_BUFFER_ANDROID \
    VK_ANDROID_NATIVE_BUFFER_ENUM(VkStructureType, 0)
#define VK_STRUCTURE_TYPE_SWAPCHAIN_IMAGE_CREATE_INFO_ANDROID \
    VK_ANDROID_NATIVE_BUFFER_ENUM(VkStructureType, 1)
#define VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENTATION_PROPERTIES_ANDROID \
    VK_ANDROID_NATIVE_BUFFER_ENUM(VkStructureType, 2)
#define VK_STRUCTURE_TYPE_GRALLOC_USAGE_INFO_ANDROID \
    VK_ANDROID_NATIVE_BUFFER_ENUM(VkStructureType, 3)
#define VK_STRUCTURE_TYPE_GRALLOC_USAGE_INFO_2_ANDROID \
    VK_ANDROID_NATIVE_BUFFER_ENUM(VkStructureType, 4)

typedef enum VkSwapchainImageUsageFlagBitsANDROID {
    VK_SWAPCHAIN_IMAGE_USAGE_SHARED_BIT_ANDROID = 0x00000001,
    VK_SWAPCHAIN_IMAGE_USAGE_FLAG_BITS_MAX_ENUM_ANDROID = 0x7FFFFFFF,
} VkSwapchainImageUsageFlagBitsANDROID;

typedef VkFlags VkSwapchainImageUsageFlagsANDROID;

typedef struct VkNativeBufferUsage2ANDROID {
    uint64_t consumer;
    uint64_t producer;
} VkNativeBufferUsage2ANDROID;

typedef struct VkNativeBufferANDROID {
    VkStructureType sType;
    const void* pNext;
    buffer_handle_t handle;
    int stride;
    int format;
    int usage;
    VkNativeBufferUsage2ANDROID usage2;
    uint64_t usage3;
    struct AHardwareBuffer* ahb;
} VkNativeBufferANDROID;

typedef struct VkSwapchainImageCreateInfoANDROID {
    VkStructureType sType;
    const void* pNext;
    VkSwapchainImageUsageFlagsANDROID usage;
} VkSwapchainImageCreateInfoANDROID;

typedef struct VkPhysicalDevicePresentationPropertiesANDROID {
    VkStructureType sType;
    const void* pNext;
    VkBool32 sharedImage;
} VkPhysicalDevicePresentationPropertiesANDROID;

typedef struct VkGrallocUsageInfoANDROID {
    VkStructureType sType;
    const void* pNext;
    VkFormat format;
    VkImageUsageFlags imageUsage;
} VkGrallocUsageInfoANDROID;

typedef struct VkGrallocUsageInfo2ANDROID {
    VkStructureType sType;
    const void* pNext;
    VkFormat format;
    VkImageUsageFlags imageUsage;
    VkSwapchainImageUsageFlagsANDROID swapchainImageUsage;
} VkGrallocUsageInfo2ANDROID;

#define VK_ANDROID_external_memory_android_hardware_buffer 1
#define VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_SPEC_VERSION 5
#define VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME \
    "VK_ANDROID_external_memory_android_hardware_buffer"

typedef struct VkAndroidHardwareBufferUsageANDROID {
    VkStructureType sType;
    void* pNext;
    uint64_t androidHardwareBufferUsage;
} VkAndroidHardwareBufferUsageANDROID;

typedef struct VkAndroidHardwareBufferPropertiesANDROID {
    VkStructureType sType;
    void* pNext;
    VkDeviceSize allocationSize;
    uint32_t memoryTypeBits;
} VkAndroidHardwareBufferPropertiesANDROID;

typedef struct VkAndroidHardwareBufferFormatPropertiesANDROID {
    VkStructureType sType;
    void* pNext;
    VkFormat format;
    uint64_t externalFormat;
    VkFormatFeatureFlags formatFeatures;
    VkComponentMapping samplerYcbcrConversionComponents;
    VkSamplerYcbcrModelConversion suggestedYcbcrModel;
    VkSamplerYcbcrRange suggestedYcbcrRange;
    VkChromaLocation suggestedXChromaOffset;
    VkChromaLocation suggestedYChromaOffset;
} VkAndroidHardwareBufferFormatPropertiesANDROID;

typedef struct VkImportAndroidHardwareBufferInfoANDROID {
    VkStructureType sType;
    const void* pNext;
    struct AHardwareBuffer* buffer;
} VkImportAndroidHardwareBufferInfoANDROID;

typedef struct VkMemoryGetAndroidHardwareBufferInfoANDROID {
    VkStructureType sType;
    const void* pNext;
    VkDeviceMemory memory;
} VkMemoryGetAndroidHardwareBufferInfoANDROID;

typedef struct VkExternalFormatANDROID {
    VkStructureType sType;
    void* pNext;
    uint64_t externalFormat;
} VkExternalFormatANDROID;

typedef struct VkAndroidHardwareBufferFormatProperties2ANDROID {
    VkStructureType sType;
    void* pNext;
    VkFormat format;
    uint64_t externalFormat;
    VkFormatFeatureFlags2 formatFeatures;
    VkComponentMapping samplerYcbcrConversionComponents;
    VkSamplerYcbcrModelConversion suggestedYcbcrModel;
    VkSamplerYcbcrRange suggestedYcbcrRange;
    VkChromaLocation suggestedXChromaOffset;
    VkChromaLocation suggestedYChromaOffset;
} VkAndroidHardwareBufferFormatProperties2ANDROID;

typedef VkResult (VKAPI_PTR *PFN_vkGetAndroidHardwareBufferPropertiesANDROID)(
    VkDevice device, const struct AHardwareBuffer* buffer,
    VkAndroidHardwareBufferPropertiesANDROID* pProperties);
typedef VkResult (VKAPI_PTR *PFN_vkGetMemoryAndroidHardwareBufferANDROID)(
    VkDevice device,
    const VkMemoryGetAndroidHardwareBufferInfoANDROID* pInfo,
    struct AHardwareBuffer** pBuffer);

#ifdef __cplusplus
}
#endif

#endif
