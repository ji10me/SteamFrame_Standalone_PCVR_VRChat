/* Read-only Vulkan foveation capability query. No device, image or VR session. */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan_core.h>
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern int printf(const char *, ...);
extern int fflush(void *);
extern unsigned int sleep(unsigned int);

static int equal(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

int main(void) {
    void *library = dlopen("libvulkan.so.1", 2);
    if (!library) return 1;
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)dlsym(library, "vkCreateInstance");
    PFN_vkGetInstanceProcAddr get = (PFN_vkGetInstanceProcAddr)dlsym(library, "vkGetInstanceProcAddr");
    if (!create || !get) return 2;
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "frame-foveation-capabilities", .apiVersion = VK_API_VERSION_1_3 };
    VkInstanceCreateInfo info = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app };
    VkInstance instance = VK_NULL_HANDLE;
    VkResult result = create(&info, 0, &instance);
    if (result != VK_SUCCESS) { printf("vkCreateInstance=%d\n", result); return 3; }
#define GET(N) PFN_vk##N p_##N = (PFN_vk##N)get(instance, "vk" #N)
    GET(DestroyInstance);
    GET(EnumeratePhysicalDevices);
    GET(EnumerateDeviceExtensionProperties);
    GET(GetPhysicalDeviceFeatures2);
    GET(GetPhysicalDeviceProperties2);
    GET(GetPhysicalDeviceFragmentShadingRatesKHR);
#undef GET
    if (!p_DestroyInstance) return 4;
    int status = 5;
    if (!p_EnumeratePhysicalDevices || !p_EnumerateDeviceExtensionProperties ||
        !p_GetPhysicalDeviceFeatures2 || !p_GetPhysicalDeviceProperties2) goto done;
    uint32_t count = 8;
    VkPhysicalDevice devices[8];
    result = p_EnumeratePhysicalDevices(instance, &count, devices);
    if (result != VK_SUCCESS || !count) goto done;
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDevice device = devices[i];
        uint32_t extension_count = 512;
        VkExtensionProperties extensions[512];
        result = p_EnumerateDeviceExtensionProperties(device, 0, &extension_count, extensions);
        if (result != VK_SUCCESS) goto done;
        int fsr = 0, fdm = 0;
        for (uint32_t j = 0; j < extension_count; ++j) {
            fsr |= equal(extensions[j].extensionName, VK_KHR_FRAGMENT_SHADING_RATE_EXTENSION_NAME);
            fdm |= equal(extensions[j].extensionName, VK_EXT_FRAGMENT_DENSITY_MAP_EXTENSION_NAME);
        }
        VkPhysicalDeviceFragmentShadingRateFeaturesKHR shading = {
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADING_RATE_FEATURES_KHR };
        VkPhysicalDeviceFragmentDensityMapFeaturesEXT density = {
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_DENSITY_MAP_FEATURES_EXT };
        VkPhysicalDeviceFeatures2 features = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
        if (fdm) { density.pNext = features.pNext; features.pNext = &density; }
        if (fsr) { shading.pNext = features.pNext; features.pNext = &shading; }
        p_GetPhysicalDeviceFeatures2(device, &features);
        VkPhysicalDeviceFragmentShadingRatePropertiesKHR shading_props = {
            .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADING_RATE_PROPERTIES_KHR };
        VkPhysicalDeviceProperties2 props = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
            .pNext = fsr ? &shading_props : 0 };
        p_GetPhysicalDeviceProperties2(device, &props);
        printf("device_index=%u vendor_id=%u device_id=%u\n", i, props.properties.vendorID, props.properties.deviceID);
        printf("fragment_shading_rate_extension=%d pipeline=%u primitive=%u attachment=%u\n",
            fsr, shading.pipelineFragmentShadingRate, shading.primitiveFragmentShadingRate,
            shading.attachmentFragmentShadingRate);
        printf("fragment_density_map_extension=%d density=%u dynamic=%u nonsubsampled=%u\n",
            fdm, density.fragmentDensityMap, density.fragmentDensityMapDynamic,
            density.fragmentDensityMapNonSubsampledImages);
        if (fsr) {
            printf("attachment_texel_min=%ux%u max=%ux%u max_fragment=%ux%u\n",
                shading_props.minFragmentShadingRateAttachmentTexelSize.width,
                shading_props.minFragmentShadingRateAttachmentTexelSize.height,
                shading_props.maxFragmentShadingRateAttachmentTexelSize.width,
                shading_props.maxFragmentShadingRateAttachmentTexelSize.height,
                shading_props.maxFragmentSize.width, shading_props.maxFragmentSize.height);
            if (!p_GetPhysicalDeviceFragmentShadingRatesKHR) goto done;
            uint32_t rate_count = 64;
            VkPhysicalDeviceFragmentShadingRateKHR rates[64] = {0};
            for (uint32_t j = 0; j < rate_count; ++j)
                rates[j].sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADING_RATE_KHR;
            result = p_GetPhysicalDeviceFragmentShadingRatesKHR(device, &rate_count, rates);
            if (result != VK_SUCCESS) goto done;
            for (uint32_t j = 0; j < rate_count; ++j)
                printf("shading_rate=%ux%u sample_count_mask=0x%x\n",
                    rates[j].fragmentSize.width, rates[j].fragmentSize.height, rates[j].sampleCounts);
        }
    }
    status = 0;
done:
    fflush(0);
    sleep(2); /* Let the isolated runner inspect only this probe's library ABI. */
    p_DestroyInstance(instance, 0);
    return status;
}
