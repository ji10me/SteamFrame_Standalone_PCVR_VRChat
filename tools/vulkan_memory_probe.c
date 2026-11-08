/* Native physical-device memory query. No device, queue, image or VR session. */
#include <vulkan/vulkan.h>
#ifdef FRAME_PROBE_FREESTANDING
#include <stddef.h>
extern int printf(const char *, ...);
extern int putchar(int);
extern void *calloc(size_t, size_t);
extern void free(void *);
extern int strcmp(const char *, const char *);
#else
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#endif

static void json_string(const char *text) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') putchar('\\');
        if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

int main(void) {
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                            .pApplicationName = "frame-memory-query", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo create = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance instance;
    VkResult status = vkCreateInstance(&create, NULL, &instance);
    if (status != VK_SUCCESS) {
        printf("Vulkan instance query failed: %d\n", status);
        return 1;
    }
    uint32_t count = 0;
    status = vkEnumeratePhysicalDevices(instance, &count, NULL);
    if (status != VK_SUCCESS || count == 0 || count > 8) {
        vkDestroyInstance(instance, NULL);
        return 2;
    }
    VkPhysicalDevice devices[8];
    status = vkEnumeratePhysicalDevices(instance, &count, devices);
    if (status != VK_SUCCESS) {
        vkDestroyInstance(instance, NULL);
        return 3;
    }
    printf("{\"devices\":[");
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties properties;
        vkGetPhysicalDeviceProperties(devices[i], &properties);
        uint32_t ext_count = 0;
        status = vkEnumerateDeviceExtensionProperties(devices[i], NULL, &ext_count, NULL);
        VkExtensionProperties *extensions = NULL;
        int has_budget = 0;
        if (status == VK_SUCCESS && ext_count && ext_count <= 4096) {
            extensions = calloc(ext_count, sizeof(*extensions));
            if (extensions && vkEnumerateDeviceExtensionProperties(devices[i], NULL, &ext_count, extensions) == VK_SUCCESS) {
                for (uint32_t j = 0; j < ext_count; ++j)
                    has_budget |= strcmp(extensions[j].extensionName, VK_EXT_MEMORY_BUDGET_EXTENSION_NAME) == 0;
            }
        }
        free(extensions);
        VkPhysicalDeviceMemoryBudgetPropertiesEXT budget = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT};
        VkPhysicalDeviceMemoryProperties2 memory = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2,
                                                    .pNext = has_budget ? &budget : NULL};
        vkGetPhysicalDeviceMemoryProperties2(devices[i], &memory);
        if (i) putchar(',');
        printf("{\"name\":");
        json_string(properties.deviceName);
        printf(",\"memory_budget_supported\":%s,\"heaps\":[", has_budget ? "true" : "false");
        for (uint32_t j = 0; j < memory.memoryProperties.memoryHeapCount; ++j) {
            if (j) putchar(',');
            printf("{\"size_bytes\":%llu,\"flags\":%u", (unsigned long long)memory.memoryProperties.memoryHeaps[j].size,
                   memory.memoryProperties.memoryHeaps[j].flags);
            if (has_budget)
                printf(",\"budget_bytes\":%llu,\"usage_bytes\":%llu", (unsigned long long)budget.heapBudget[j],
                       (unsigned long long)budget.heapUsage[j]);
            putchar('}');
        }
        printf("]}");
    }
    printf("]}\n");
    vkDestroyInstance(instance, NULL);
    return 0;
}
