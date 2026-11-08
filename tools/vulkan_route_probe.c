/* Independent 64-bit Vulkan ABI probe: no game, window, rendering or VR session.
 * Uses minimal public ABI declarations so the x86 runtime needs no dev headers.
 * The parent inspects /proc/PID/maps during the final two-second hold.
 */
typedef unsigned int U32;
typedef void *Handle;
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern char *dlerror(void);
extern int printf(const char *, ...);
extern int fflush(void *);
extern unsigned int sleep(unsigned int);

struct AppInfo {
    U32 type; const void *next; const char *name; U32 version;
    const char *engine; U32 engineVersion; U32 apiVersion;
};
struct InstanceInfo {
    U32 type; const void *next; U32 flags; const struct AppInfo *app;
    U32 layerCount; const char *const *layers;
    U32 extensionCount; const char *const *extensions;
};
struct QueueFamily { U32 flags, count, timestampBits, granularity[3]; };
struct QueueInfo {
    U32 type; const void *next; U32 flags, family, count; const float *priorities;
};
struct DeviceInfo {
    U32 type; const void *next; U32 flags, queueCount; const struct QueueInfo *queues;
    U32 layerCount; const char *const *layers;
    U32 extensionCount; const char *const *extensions; const void *features;
};

int main(void) {
    void *lib = dlopen("libvulkan.so.1", 2);
    if (!lib) { printf("dlopen failed: %s\n", dlerror()); return 1; }
    int (*create)(const struct InstanceInfo *, const void *, Handle *) = dlsym(lib, "vkCreateInstance");
    int (*enumerate)(Handle, U32 *, Handle *) = dlsym(lib, "vkEnumeratePhysicalDevices");
    void (*properties)(Handle, void *) = dlsym(lib, "vkGetPhysicalDeviceProperties");
    void (*families)(Handle, U32 *, struct QueueFamily *) = dlsym(lib, "vkGetPhysicalDeviceQueueFamilyProperties");
    int (*createDevice)(Handle, const struct DeviceInfo *, const void *, Handle *) = dlsym(lib, "vkCreateDevice");
    void (*destroyDevice)(Handle, const void *) = dlsym(lib, "vkDestroyDevice");
    void (*destroy)(Handle, const void *) = dlsym(lib, "vkDestroyInstance");
    void *(*instanceProc)(Handle, const char *) = dlsym(lib, "vkGetInstanceProcAddr");
    void *(*deviceProc)(Handle, const char *) = dlsym(lib, "vkGetDeviceProcAddr");
    if (!create || !enumerate || !properties || !families || !createDevice || !destroyDevice || !destroy) return 2;
    struct AppInfo app = {0, 0, "frame-pcvr-route-probe", 1, "probe", 1, (1U << 22) | (3U << 12)};
    struct InstanceInfo info = {1, 0, 0, &app, 0, 0, 0, 0};
    Handle instance = 0, device = 0;
    int result = create(&info, 0, &instance);
    printf("vkCreateInstance=%d\n", result);
    if (result != 0) return 3;
    U32 count = 8;
    Handle devices[8];
    result = enumerate(instance, &count, devices);
    printf("vkEnumeratePhysicalDevices=%d count=%u\n", result, count);
    if (result != 0 || !count || count > 8) { destroy(instance, 0); return 4; }
    union { unsigned long long align; unsigned char bytes[4096]; } data = {0};
    properties(devices[0], &data);
    printf("device=%.*s\n", 256, data.bytes + 20);
    U32 familyCount = 16;
    struct QueueFamily queues[16];
    families(devices[0], &familyCount, queues);
    U32 family = 0;
    for (; family < familyCount && family < 16; ++family) {
        if (queues[family].count && (queues[family].flags & 1)) break;
    }
    if (family >= familyCount || family >= 16) { destroy(instance, 0); return 5; }
    float priority = 1;
    struct QueueInfo qi = {2, 0, 0, family, 1, &priority};
    struct DeviceInfo di = {3, 0, 0, 1, &qi, 0, 0, 0, 0, 0};
    result = createDevice(devices[0], &di, 0, &device);
    printf("vkCreateDevice=%d\n", result);
    int lookup_ok = 0;
    if (result == 0 && instanceProc && deviceProc) {
        void *a = instanceProc(instance, "vkGetInstanceProcAddr");
        void *b = instanceProc(instance, "vkGetDeviceProcAddr");
        void *c = deviceProc(device, "vkGetDeviceProcAddr");
        printf("instance_self_lookup=%s\n", a ? "present" : "NULL");
        printf("instance_device_proc_lookup=%s\n", b ? "present" : "NULL");
        printf("device_self_lookup=%s\n", c ? "present" : "NULL");
        lookup_ok = a && b && c;
    }
    fflush(0);
    sleep(2);
    if (result == 0) destroyDevice(device, 0);
    destroy(instance, 0);
    return result != 0 ? 6 : lookup_ok ? 0 : 7;
}
