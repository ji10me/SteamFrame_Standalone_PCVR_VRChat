/* Private guest-side adapter for FEX Vulkan self-lookups (upstream #5940).
 * All other Vulkan exports come from the unchanged-code base thunk dependency.
 * No game, anti-cheat, driver or CPU-emulation behavior is changed here.
 */
typedef void (*Function)(void);
typedef Function (*GetProc)(void *, const char *);
extern int strcmp(const char *, const char *);
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern int printf(const char *, ...);

static GetProc base_instance, base_device;
Function vkGetInstanceProcAddr(void *, const char *);
Function vkGetDeviceProcAddr(void *, const char *);

__attribute__((constructor)) static void initialize(void) {
    void *base = dlopen("libframe_vulkan_thunk_base.so", 2);
    if (base) {
        base_instance = (GetProc)dlsym(base, "vkGetInstanceProcAddr");
        base_device = (GetProc)dlsym(base, "vkGetDeviceProcAddr");
    }
    if (!base_instance || !base_device)
        printf("frame-pcvr: Vulkan lookup adapter could not load its private base thunk\n");
}

Function vkGetInstanceProcAddr(void *instance, const char *name) {
    if (name && !strcmp(name, "vkGetInstanceProcAddr")) return (Function)vkGetInstanceProcAddr;
    if (name && !strcmp(name, "vkGetDeviceProcAddr")) return (Function)vkGetDeviceProcAddr;
    return base_instance ? base_instance(instance, name) : (Function)0;
}

Function vkGetDeviceProcAddr(void *device, const char *name) {
    if (name && !strcmp(name, "vkGetDeviceProcAddr")) return (Function)vkGetDeviceProcAddr;
    return base_device ? base_device(device, name) : (Function)0;
}
