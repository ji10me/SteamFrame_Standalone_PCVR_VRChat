/* Small Linux x86-64 ABI probe. No game data, window, rendering, or input.
 * Vulkan's public 64-bit C ABI declarations used here are intentionally small
 * so the Frame's x86 runtime sysroot needs no development headers.
 */
typedef unsigned int U32;
typedef void *Handle;
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern char *dlerror(void);
extern int printf(const char *, ...);
extern void *memcpy(void *, const void *, unsigned long);

struct AppInfo {
    U32 type; const void *next; const char *name; U32 version;
    const char *engine; U32 engineVersion; U32 apiVersion;
};
struct InstanceInfo {
    U32 type; const void *next; U32 flags; const struct AppInfo *app;
    U32 layerCount; const char *const *layers;
    U32 extensionCount; const char *const *extensions;
};

int main(void) {
    void *lib = dlopen("libvulkan.so.1", 2);
    if (!lib) { printf("dlopen failed: %s\n", dlerror()); return 1; }
    int (*create)(const struct InstanceInfo *, const void *, Handle *) = dlsym(lib, "vkCreateInstance");
    int (*enumerate)(Handle, U32 *, Handle *) = dlsym(lib, "vkEnumeratePhysicalDevices");
    void (*properties)(Handle, void *) = dlsym(lib, "vkGetPhysicalDeviceProperties");
    void (*destroy)(Handle, const void *) = dlsym(lib, "vkDestroyInstance");
    if (!create || !enumerate || !properties || !destroy) return 2;
    struct AppInfo app = {0, 0, "frame-pcvr-smoke", 1, "probe", 1, 1U << 22};
    struct InstanceInfo info = {1, 0, 0, &app, 0, 0, 0, 0};
    Handle instance = 0;
    int result = create(&info, 0, &instance);
    printf("vkCreateInstance=%d\n", result);
    if (result != 0) return 3;
    U32 count = 8;
    Handle devices[8];
    result = enumerate(instance, &count, devices);
    printf("vkEnumeratePhysicalDevices=%d count=%u\n", result, count);
    if (result != 0 || !count || count > 8) { destroy(instance, 0); return 4; }
    int gpu = 0;
    for (U32 i = 0; i < count; ++i) {
        /* VkPhysicalDeviceProperties begins with 5 uint32 fields and a
         * VK_MAX_PHYSICAL_DEVICE_NAME_SIZE(256) name. Reserve ample aligned
         * storage for the full implementation-written properties structure.
         */
        union { unsigned long long align; unsigned char bytes[4096]; } data = {0};
        properties(devices[i], &data);
        U32 vendor, device, type;
        memcpy(&vendor, data.bytes + 8, 4);
        memcpy(&device, data.bytes + 12, 4);
        memcpy(&type, data.bytes + 16, 4);
        printf("device=%.*s vendor=%x id=%x type=%u\n", 256, data.bytes + 20, vendor, device, type);
        if (type == 1 || type == 2 || type == 3) gpu = 1;
    }
    destroy(instance, 0);
    return gpu ? 0 : 5;
}
