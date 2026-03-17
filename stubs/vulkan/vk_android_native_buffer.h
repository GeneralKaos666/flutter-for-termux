/*
 * Stub header for Termux/cross-compilation builds.
 *
 * vk_android_native_buffer.h is an Android platform-internal header not
 * included in the public NDK.  SwiftShader includes it when __ANDROID__ is
 * defined (which happens because we cross-compile with
 * --target=aarch64-linux-androidXX).  This stub provides the minimum type
 * definitions needed so that SwiftShader compiles cleanly on a Termux/Linux
 * host where the real AOSP gralloc headers are unavailable.
 *
 * Based on the AOSP vk_android_native_buffer.h spec version 11 and the Mesa
 * project's equivalent stub.
 */

#ifndef __VK_ANDROID_NATIVE_BUFFER_H__
#define __VK_ANDROID_NATIVE_BUFFER_H__

#include <vulkan/vulkan.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* buffer_handle_t: opaque handle to an Android gralloc buffer.
 * On non-AOSP builds (like Termux) we use void* as a neutral placeholder. */
typedef void* buffer_handle_t;

/* AHardwareBuffer forward declaration */
struct AHardwareBuffer;

#define VK_ANDROID_native_buffer 1

#define VK_ANDROID_NATIVE_BUFFER_EXTENSION_NUMBER 11
#define VK_ANDROID_NATIVE_BUFFER_SPEC_VERSION     11
#define VK_ANDROID_NATIVE_BUFFER_EXTENSION_NAME  "VK_ANDROID_native_buffer"

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
    VK_SWAPCHAIN_IMAGE_USAGE_SHARED_BIT_ANDROID       = 0x00000001,
    VK_SWAPCHAIN_IMAGE_USAGE_FLAG_BITS_MAX_ENUM_ANDROID = 0x7FFFFFFF
} VkSwapchainImageUsageFlagBitsANDROID;
typedef VkFlags VkSwapchainImageUsageFlagsANDROID;

typedef struct {
    uint64_t consumer;
    uint64_t producer;
} VkNativeBufferUsage2ANDROID;

typedef struct {
    VkStructureType             sType;
    const void*                 pNext;
    buffer_handle_t             handle;
    int                         stride;
    int                         format;
    int                         usage;      /* DEPRECATED in SPEC_VERSION 6  */
    VkNativeBufferUsage2ANDROID usage2;     /* DEPRECATED in SPEC_VERSION 9  */
    uint64_t                    usage3;     /* ADDED    in SPEC_VERSION 9    */
    struct AHardwareBuffer*     ahb;        /* ADDED    in SPEC_VERSION 11   */
} VkNativeBufferANDROID;

typedef struct {
    VkStructureType                   sType;
    const void*                       pNext;
    VkSwapchainImageUsageFlagsANDROID usage;
} VkSwapchainImageCreateInfoANDROID;

typedef struct {
    VkStructureType sType;
    const void*     pNext;
    VkBool32        sharedImage;
} VkPhysicalDevicePresentationPropertiesANDROID;

typedef struct {
    VkStructureType   sType;
    const void*       pNext;
    VkFormat          format;
    VkImageUsageFlags imageUsage;
} VkGrallocUsageInfoANDROID;

typedef struct {
    VkStructureType                   sType;
    const void*                       pNext;
    VkFormat                          format;
    VkImageUsageFlags                 imageUsage;
    VkSwapchainImageUsageFlagsANDROID swapchainImageUsage;
} VkGrallocUsageInfo2ANDROID;

/* Function pointer types for the extension entry points */
typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsageANDROID)(
    VkDevice device, VkFormat format, VkImageUsageFlags imageUsage,
    int* grallocUsage);
typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsage2ANDROID)(
    VkDevice device, VkFormat format, VkImageUsageFlags imageUsage,
    VkSwapchainImageUsageFlagsANDROID swapchainImageUsage,
    uint64_t* grallocConsumerUsage, uint64_t* grallocProducerUsage);
typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsage3ANDROID)(
    VkDevice device, const VkGrallocUsageInfo2ANDROID* grallocUsageInfo,
    uint64_t* grallocConsumerUsage, uint64_t* grallocProducerUsage);
typedef VkResult (VKAPI_PTR *PFN_vkAcquireImageANDROID)(
    VkDevice device, VkImage image, int nativeFenceFd,
    VkSemaphore semaphore, VkFence fence);
typedef VkResult (VKAPI_PTR *PFN_vkQueueSignalReleaseImageANDROID)(
    VkQueue queue, uint32_t waitSemaphoreCount,
    const VkSemaphore* pWaitSemaphores, VkImage image,
    int* pNativeFenceFd);

/* VK_ANDROID_external_memory_android_hardware_buffer types.
 *
 * These are normally provided by vulkan_android.h (included by vulkan.h when
 * VK_USE_PLATFORM_ANDROID_KHR is defined).  When the NDK sysroot Vulkan headers
 * already define them the guard below prevents redefinition; if they are absent
 * (e.g. older NDK sources path was used instead of the sysroot path) the stub
 * definitions here ensure SwiftShader can still compile. */
#ifndef VK_ANDROID_external_memory_android_hardware_buffer
#define VK_ANDROID_external_memory_android_hardware_buffer 1

#define VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_SPEC_VERSION 5
#define VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME \
    "VK_ANDROID_external_memory_android_hardware_buffer"

#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_USAGE_ANDROID \
    ((VkStructureType)1000129000)
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID \
    ((VkStructureType)1000129001)
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_ANDROID \
    ((VkStructureType)1000129002)
#define VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID \
    ((VkStructureType)1000129003)
#define VK_STRUCTURE_TYPE_MEMORY_GET_ANDROID_HARDWARE_BUFFER_INFO_ANDROID \
    ((VkStructureType)1000129004)
#define VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID \
    ((VkStructureType)1000129005)
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_2_ANDROID \
    ((VkStructureType)1000129006)

typedef struct VkAndroidHardwareBufferUsageANDROID {
    VkStructureType    sType;
    void*              pNext;
    uint64_t           androidHardwareBufferUsage;
} VkAndroidHardwareBufferUsageANDROID;

typedef struct VkAndroidHardwareBufferPropertiesANDROID {
    VkStructureType    sType;
    void*              pNext;
    VkDeviceSize       allocationSize;
    uint32_t           memoryTypeBits;
} VkAndroidHardwareBufferPropertiesANDROID;

typedef struct VkAndroidHardwareBufferFormatPropertiesANDROID {
    VkStructureType                  sType;
    void*                            pNext;
    VkFormat                         format;
    uint64_t                         externalFormat;
    VkFormatFeatureFlags             formatFeatures;
    VkComponentMapping               samplerYcbcrConversionComponents;
    VkSamplerYcbcrModelConversion    suggestedYcbcrModel;
    VkSamplerYcbcrRange              suggestedYcbcrRange;
    VkChromaLocation                 suggestedXChromaOffset;
    VkChromaLocation                 suggestedYChromaOffset;
} VkAndroidHardwareBufferFormatPropertiesANDROID;

typedef struct VkImportAndroidHardwareBufferInfoANDROID {
    VkStructureType          sType;
    const void*              pNext;
    struct AHardwareBuffer*  buffer;
} VkImportAndroidHardwareBufferInfoANDROID;

typedef struct VkMemoryGetAndroidHardwareBufferInfoANDROID {
    VkStructureType    sType;
    const void*        pNext;
    VkDeviceMemory     memory;
} VkMemoryGetAndroidHardwareBufferInfoANDROID;

typedef struct VkExternalFormatANDROID {
    VkStructureType    sType;
    const void*        pNext;
    uint64_t           externalFormat;
} VkExternalFormatANDROID;

typedef struct VkAndroidHardwareBufferFormatProperties2ANDROID {
    VkStructureType                  sType;
    void*                            pNext;
    VkFormat                         format;
    uint64_t                         externalFormat;
    VkFormatFeatureFlags2            formatFeatures;
    VkComponentMapping               samplerYcbcrConversionComponents;
    VkSamplerYcbcrModelConversion    suggestedYcbcrModel;
    VkSamplerYcbcrRange              suggestedYcbcrRange;
    VkChromaLocation                 suggestedXChromaOffset;
    VkChromaLocation                 suggestedYChromaOffset;
} VkAndroidHardwareBufferFormatProperties2ANDROID;

typedef VkResult (VKAPI_PTR *PFN_vkGetAndroidHardwareBufferPropertiesANDROID)(
    VkDevice device, const struct AHardwareBuffer* buffer,
    VkAndroidHardwareBufferPropertiesANDROID* pProperties);
typedef VkResult (VKAPI_PTR *PFN_vkGetMemoryAndroidHardwareBufferANDROID)(
    VkDevice device,
    const VkMemoryGetAndroidHardwareBufferInfoANDROID* pInfo,
    struct AHardwareBuffer** pBuffer);

#endif /* VK_ANDROID_external_memory_android_hardware_buffer */

#ifdef __cplusplus
}
#endif

#endif /* __VK_ANDROID_NATIVE_BUFFER_H__ */
