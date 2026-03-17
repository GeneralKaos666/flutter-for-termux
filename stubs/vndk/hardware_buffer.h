/*
 * Stub header for Termux/cross-compilation builds.
 *
 * vndk/hardware_buffer.h is a VNDK (Vendor NDK) header that extends the public
 * android/hardware_buffer.h with platform-internal functions not exposed in the
 * public NDK.  SwiftShader includes it (inside #ifdef __ANDROID__) for
 * AHardwareBuffer_createFromHandle and related API.
 *
 * Because we cross-compile with --target=aarch64-linux-androidXX the
 * preprocessor defines __ANDROID__, so this stub is needed so that
 * SwiftShader's VkImage.cpp and VkDeviceMemoryExternalAndroid.hpp compile
 * cleanly on a Termux/Linux host where the real VNDK headers are unavailable.
 *
 * The public AHardwareBuffer types (AHardwareBuffer_Desc, AHardwareBuffer_Planes,
 * AHardwareBuffer_lockPlanes, etc.) are re-exported from the NDK's own
 * android/hardware_buffer.h.  Only the VNDK-exclusive
 * AHardwareBuffer_createFromHandle symbol is added here.
 *
 * Note: build.py passes -D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__ globally so
 * that API-level-gated NDK functions (like AHardwareBuffer_lockPlanes, API 29)
 * do not cause hard compile errors when targeting API 26.
 */

#ifndef ANDROID_VNDK_HARDWARE_BUFFER_H
#define ANDROID_VNDK_HARDWARE_BUFFER_H

/* Pull in the public NDK AHardwareBuffer API (Desc, Planes, lock, etc.) */
#include <android/hardware_buffer.h>

__BEGIN_DECLS

/* ----------------------------------------------------------------
 * VNDK-only extension: create an AHardwareBuffer from a native handle.
 * Not in the public NDK; declared here so SwiftShader can compile.
 * ---------------------------------------------------------------- */

typedef enum AHardwareBufferCreateFromHandleMethod {
    AHARDWAREBUFFER_CREATE_FROM_HANDLE_METHOD_REGISTER = 0,
    AHARDWAREBUFFER_CREATE_FROM_HANDLE_METHOD_CLONE    = 1,
} AHardwareBufferCreateFromHandleMethod;

/*
 * native_handle_t: opaque resource handle.  The full definition lives in
 * cutils/native_handle.h (AOSP internal); forward-declare as a struct so
 * that the pointer parameter below can be typed without the full definition.
 */
#ifndef _NATIVE_HANDLE_DECLARED
#define _NATIVE_HANDLE_DECLARED
typedef struct native_handle native_handle_t;
#endif

int AHardwareBuffer_createFromHandle(
    const AHardwareBuffer_Desc  *_Nonnull  desc,
    const native_handle_t       *_Nonnull  handle,
    int32_t                                method,
    AHardwareBuffer *_Nullable *_Nonnull   outBuffer);

__END_DECLS

#endif /* ANDROID_VNDK_HARDWARE_BUFFER_H */
