/* Synthetic offscreen GPU image test. No game, window, VR session or shaders.
 * Clear through dynamic rendering, copy to coherent host memory, verify pixels.
 */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan_core.h>
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern int printf(const char *, ...);
extern int fflush(void *);
extern unsigned int sleep(unsigned int);

#define INSTANCE_FUNCTIONS(X) X(DestroyInstance) X(EnumeratePhysicalDevices) \
 X(GetPhysicalDeviceQueueFamilyProperties) X(GetPhysicalDeviceMemoryProperties) \
 X(GetPhysicalDeviceFeatures2) X(CreateDevice)
#define DEVICE_FUNCTIONS(X) X(DestroyDevice) X(GetDeviceQueue) X(CreateImage) X(DestroyImage) \
 X(GetImageMemoryRequirements) X(AllocateMemory) X(FreeMemory) X(BindImageMemory) \
 X(CreateImageView) X(DestroyImageView) X(CreateBuffer) X(DestroyBuffer) \
 X(GetBufferMemoryRequirements) X(BindBufferMemory) X(MapMemory) X(UnmapMemory) \
 X(CreateCommandPool) X(DestroyCommandPool) X(AllocateCommandBuffers) X(BeginCommandBuffer) \
 X(EndCommandBuffer) X(CmdPipelineBarrier) X(CmdBeginRendering) X(CmdEndRendering) \
 X(CmdClearAttachments) X(CmdCopyImageToBuffer) X(QueueSubmit) X(QueueWaitIdle)
#ifdef FRAME_VRS_SECONDARY_PROBE
#define VRS_FUNCTIONS(X) X(CmdExecuteCommands) X(CmdSetFragmentShadingRateKHR) X(CmdClearColorImage)
#else
#define VRS_FUNCTIONS(X)
#endif
#define DECLARE(N) static PFN_vk##N p_##N;
INSTANCE_FUNCTIONS(DECLARE)
DEVICE_FUNCTIONS(DECLARE)
VRS_FUNCTIONS(DECLARE)
#undef DECLARE
static VkPhysicalDeviceMemoryProperties memory;

static uint32_t memory_type(uint32_t bits, VkMemoryPropertyFlags flags) {
    for (uint32_t i = 0; i < memory.memoryTypeCount; ++i)
        if ((bits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & flags) == flags) return i;
    return ~0u;
}

static int test_image(VkDevice device, VkQueue queue, uint32_t family, uint32_t width, uint32_t height, uint32_t layers) {
    VkImage image = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory image_memory = VK_NULL_HANDLE, host_memory = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
#ifdef FRAME_VRS_SECONDARY_PROBE
    VkImage rate_image = VK_NULL_HANDLE;
    VkImageView rate_view = VK_NULL_HANDLE;
    VkDeviceMemory rate_memory = VK_NULL_HANDLE;
#endif
    int ok = 0;
    VkResult result;
#define CHECK(CALL) do { result = (CALL); if (result != VK_SUCCESS) { printf(#CALL " failed=%d\n", result); goto done; } } while (0)
    VkImageCreateInfo image_info = { .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = {width, height, 1}, .mipLevels = 1, .arrayLayers = layers,
        .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED };
    CHECK(p_CreateImage(device, &image_info, 0, &image));
    VkMemoryRequirements requirements;
    p_GetImageMemoryRequirements(device, image, &requirements);
    uint32_t type = memory_type(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (type == ~0u) goto done;
    VkMemoryAllocateInfo allocation = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size, .memoryTypeIndex = type };
    CHECK(p_AllocateMemory(device, &allocation, 0, &image_memory));
    CHECK(p_BindImageMemory(device, image, image_memory, 0));
    VkImageViewCreateInfo view_info = { .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image = image, .viewType = layers == 1 ? VK_IMAGE_VIEW_TYPE_2D : VK_IMAGE_VIEW_TYPE_2D_ARRAY,
        .format = image_info.format, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, layers} };
    CHECK(p_CreateImageView(device, &view_info, 0, &view));
#ifdef FRAME_VRS_SECONDARY_PROBE
    VkImageCreateInfo rate_info = image_info;
    rate_info.format = VK_FORMAT_R8_UINT;
    rate_info.extent = (VkExtent3D){(width + 7) / 8, (height + 7) / 8, 1};
    rate_info.usage = VK_IMAGE_USAGE_FRAGMENT_SHADING_RATE_ATTACHMENT_BIT_KHR | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    CHECK(p_CreateImage(device, &rate_info, 0, &rate_image));
    p_GetImageMemoryRequirements(device, rate_image, &requirements);
    type = memory_type(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (type == ~0u) goto done;
    allocation.allocationSize = requirements.size; allocation.memoryTypeIndex = type;
    CHECK(p_AllocateMemory(device, &allocation, 0, &rate_memory));
    CHECK(p_BindImageMemory(device, rate_image, rate_memory, 0));
    VkImageViewCreateInfo rate_view_info = view_info;
    rate_view_info.image = rate_image; rate_view_info.format = VK_FORMAT_R8_UINT;
    rate_view_info.subresourceRange.layerCount = 1; /* Shared map: layered VRS is unsupported on Frame. */
    CHECK(p_CreateImageView(device, &rate_view_info, 0, &rate_view));
#endif
    VkDeviceSize bytes = (VkDeviceSize)width * height * layers * 4;
    VkBufferCreateInfo buffer_info = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = bytes, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE };
    CHECK(p_CreateBuffer(device, &buffer_info, 0, &buffer));
    p_GetBufferMemoryRequirements(device, buffer, &requirements);
    type = memory_type(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (type == ~0u) goto done;
    allocation.allocationSize = requirements.size; allocation.memoryTypeIndex = type;
    CHECK(p_AllocateMemory(device, &allocation, 0, &host_memory));
    CHECK(p_BindBufferMemory(device, buffer, host_memory, 0));
    VkCommandPoolCreateInfo pool_info = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = family };
    CHECK(p_CreateCommandPool(device, &pool_info, 0, &pool));
    VkCommandBufferAllocateInfo command_info = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
    VkCommandBuffer command;
    CHECK(p_AllocateCommandBuffers(device, &command_info, &command));
    VkCommandBufferBeginInfo begin = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    CHECK(p_BeginCommandBuffer(command, &begin));
    VkImageMemoryBarrier barrier = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image, .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
        .dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, .subresourceRange = view_info.subresourceRange };
    p_CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                         0, 0, 0, 0, 0, 1, &barrier);
#ifdef FRAME_VRS_SECONDARY_PROBE
    VkImageMemoryBarrier rate_barrier = barrier;
    rate_barrier.image = rate_image;
    rate_barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    rate_barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    p_CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                        0, 0, 0, 0, 0, 1, &rate_barrier);
    VkClearColorValue rate_value = {.uint32 = {5, 0, 0, 0}}; /* 2x2 */
    p_CmdClearColorImage(command, rate_image, VK_IMAGE_LAYOUT_GENERAL, &rate_value, 1, &view_info.subresourceRange);
    rate_barrier.oldLayout = VK_IMAGE_LAYOUT_GENERAL;
    rate_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    rate_barrier.dstAccessMask = VK_ACCESS_FRAGMENT_SHADING_RATE_ATTACHMENT_READ_BIT_KHR;
    p_CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_FRAGMENT_SHADING_RATE_ATTACHMENT_BIT_KHR,
                        0, 0, 0, 0, 0, 1, &rate_barrier);
#endif
    VkRenderingAttachmentInfo attachment = { .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
        .imageView = view, .imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
        .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
        .clearValue = {.color = {.float32 = {0, 0, 0, 1}}} };
    VkRenderingInfo rendering = { .sType = VK_STRUCTURE_TYPE_RENDERING_INFO,
        .renderArea = {{0, 0}, {width, height}}, .layerCount = layers,
        .colorAttachmentCount = 1, .pColorAttachments = &attachment };
    VkCommandBuffer drawing = command;
#ifdef FRAME_VRS_SECONDARY_PROBE
    VkRenderingFragmentShadingRateAttachmentInfoKHR rate_attachment = {
        .sType = VK_STRUCTURE_TYPE_RENDERING_FRAGMENT_SHADING_RATE_ATTACHMENT_INFO_KHR,
        .imageView = rate_view, .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
        .shadingRateAttachmentTexelSize = {8, 8}};
    rendering.pNext = &rate_attachment;
    rendering.flags = VK_RENDERING_CONTENTS_SECONDARY_COMMAND_BUFFERS_BIT;
    command_info.level = VK_COMMAND_BUFFER_LEVEL_SECONDARY;
    CHECK(p_AllocateCommandBuffers(device, &command_info, &drawing));
    VkCommandBufferInheritanceRenderingInfo inherited_rendering = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_INHERITANCE_RENDERING_INFO,
        .colorAttachmentCount = 1, .pColorAttachmentFormats = &image_info.format,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
    VkCommandBufferInheritanceInfo inherited = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_INHERITANCE_INFO, .pNext = &inherited_rendering};
    VkCommandBufferBeginInfo secondary_begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_RENDER_PASS_CONTINUE_BIT, .pInheritanceInfo = &inherited};
    CHECK(p_BeginCommandBuffer(drawing, &secondary_begin));
    VkExtent2D fragment = {1, 1};
    VkFragmentShadingRateCombinerOpKHR combine[2] = {
        VK_FRAGMENT_SHADING_RATE_COMBINER_OP_KEEP_KHR, VK_FRAGMENT_SHADING_RATE_COMBINER_OP_REPLACE_KHR};
    p_CmdSetFragmentShadingRateKHR(drawing, &fragment, combine);
#else
    p_CmdBeginRendering(command, &rendering);
#endif
    for (uint32_t eye = 0; eye < 2; ++eye) {
        VkClearAttachment clear = { .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .colorAttachment = 0,
            .clearValue = {.color = {.float32 = {eye == 0, 0, eye == 1, 1}}} };
        VkClearRect rectangle = { .rect = {{layers == 1 ? (int32_t)(eye * width / 2) : 0, 0},
                                         {layers == 1 ? width / 2 : width, height}},
            .baseArrayLayer = layers == 1 ? 0 : eye, .layerCount = 1 };
        p_CmdClearAttachments(drawing, 1, &clear, 1, &rectangle);
    }
#ifdef FRAME_VRS_SECONDARY_PROBE
    CHECK(p_EndCommandBuffer(drawing));
    p_CmdBeginRendering(command, &rendering);
    p_CmdExecuteCommands(command, 1, &drawing);
#endif
    p_CmdEndRendering(command);
    barrier.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    barrier.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT; barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    p_CmdPipelineBarrier(command, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, 0, 0, 0, 1, &barrier);
    VkBufferImageCopy copy = { .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, layers},
        .imageExtent = {width, height, 1} };
    p_CmdCopyImageToBuffer(command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &copy);
    VkBufferMemoryBarrier host_barrier = { .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE };
    p_CmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, 0, 1, &host_barrier, 0, 0);
    CHECK(p_EndCommandBuffer(command));
    VkSubmitInfo submit = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1, .pCommandBuffers = &command };
    CHECK(p_QueueSubmit(queue, 1, &submit, VK_NULL_HANDLE));
    CHECK(p_QueueWaitIdle(queue));
    void *mapped;
    CHECK(p_MapMemory(device, host_memory, 0, bytes, 0, &mapped));
    unsigned char *pixels = mapped;
    VkDeviceSize mismatches = 0;
    VkDeviceSize red = 0, blue = 0, black = 0, other = 0;
    for (uint32_t layer = 0; layer < layers; ++layer)
        for (uint32_t y = 0; y < height; ++y)
            for (uint32_t x = 0; x < width; ++x) {
                uint32_t eye = layers == 1 ? (x >= width / 2) : layer;
                VkDeviceSize offset = (((VkDeviceSize)layer * height + y) * width + x) * 4;
                if (pixels[offset] == 255 && !pixels[offset + 1] && !pixels[offset + 2] && pixels[offset + 3] == 255) ++red;
                else if (!pixels[offset] && !pixels[offset + 1] && pixels[offset + 2] == 255 && pixels[offset + 3] == 255) ++blue;
                else if (!pixels[offset] && !pixels[offset + 1] && !pixels[offset + 2]) ++black;
                else ++other;
                if (pixels[offset] != (eye == 0 ? 255 : 0) || pixels[offset + 1] ||
                    pixels[offset + 2] != (eye == 1 ? 255 : 0) || pixels[offset + 3] != 255) ++mismatches;
            }
    p_UnmapMemory(device, host_memory);
    printf("image=%ux%ux%u mismatched_pixels=%llu total_pixels=%llu\n", width, height, layers,
           (unsigned long long)mismatches, (unsigned long long)width * height * layers);
    printf("pixel_colors red=%llu blue=%llu black=%llu other=%llu\n",
           (unsigned long long)red, (unsigned long long)blue, (unsigned long long)black, (unsigned long long)other);
    ok = mismatches == 0;
done:
    if (pool) p_DestroyCommandPool(device, pool, 0);
#ifdef FRAME_VRS_SECONDARY_PROBE
    if (rate_view) p_DestroyImageView(device, rate_view, 0);
    if (rate_image) p_DestroyImage(device, rate_image, 0);
    if (rate_memory) p_FreeMemory(device, rate_memory, 0);
#endif
    if (view) p_DestroyImageView(device, view, 0);
    if (image) p_DestroyImage(device, image, 0);
    if (buffer) p_DestroyBuffer(device, buffer, 0);
    if (image_memory) p_FreeMemory(device, image_memory, 0);
    if (host_memory) p_FreeMemory(device, host_memory, 0);
    return ok;
#undef CHECK
}

int main(void) {
    void *library = dlopen("libvulkan.so.1", 2);
    if (!library) { printf("Vulkan loader unavailable\n"); return 1; }
    PFN_vkGetInstanceProcAddr get_instance = (PFN_vkGetInstanceProcAddr)dlsym(library, "vkGetInstanceProcAddr");
    if (!get_instance || !(get_instance = (PFN_vkGetInstanceProcAddr)get_instance(0, "vkGetInstanceProcAddr"))) return 2;
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)get_instance(0, "vkCreateInstance");
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "frame-pcvr-image-probe", .apiVersion = VK_API_VERSION_1_3 };
    VkInstanceCreateInfo info = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app };
    VkInstance instance;
    if (!create || create(&info, 0, &instance)) return 3;
#define LOAD(N) if (!(p_##N = (PFN_vk##N)get_instance(instance, "vk" #N))) return 4;
    INSTANCE_FUNCTIONS(LOAD)
#undef LOAD
    VkPhysicalDevice physical;
    uint32_t count = 1;
    VkResult result = p_EnumeratePhysicalDevices(instance, &count, &physical);
    if ((result != VK_SUCCESS && result != VK_INCOMPLETE) || !count) return 5;
    VkQueueFamilyProperties families[16]; count = 16;
    p_GetPhysicalDeviceQueueFamilyProperties(physical, &count, families);
    uint32_t family = 0;
    for (; family < count && family < 16; ++family) if (families[family].queueFlags & VK_QUEUE_GRAPHICS_BIT) break;
    if (family >= count || family >= 16) return 6;
    p_GetPhysicalDeviceMemoryProperties(physical, &memory);
    VkPhysicalDeviceVulkan13Features features = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES };
    VkPhysicalDeviceFeatures2 available = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &features };
    p_GetPhysicalDeviceFeatures2(physical, &available);
    if (!features.dynamicRendering) return 7;
    features = (VkPhysicalDeviceVulkan13Features){ .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .dynamicRendering = 1 };
#ifdef FRAME_VRS_SECONDARY_PROBE
    VkPhysicalDeviceFragmentShadingRateFeaturesKHR rate_features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADING_RATE_FEATURES_KHR,
        .attachmentFragmentShadingRate = VK_TRUE};
    features.pNext = &rate_features;
    const char *rate_extension = VK_KHR_FRAGMENT_SHADING_RATE_EXTENSION_NAME;
#endif
    float priority = 0;
    VkDeviceQueueCreateInfo queue_info = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority };
    VkDeviceCreateInfo device_info = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &features,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info };
#ifdef FRAME_VRS_SECONDARY_PROBE
    device_info.enabledExtensionCount = 1;
    device_info.ppEnabledExtensionNames = &rate_extension;
#endif
    VkDevice device;
    if (p_CreateDevice(physical, &device_info, 0, &device)) return 8;
    PFN_vkGetDeviceProcAddr get_device = (PFN_vkGetDeviceProcAddr)get_instance(instance, "vkGetDeviceProcAddr");
    if (!get_device || !(get_device = (PFN_vkGetDeviceProcAddr)get_device(device, "vkGetDeviceProcAddr"))) return 9;
#define LOAD(N) if (!(p_##N = (PFN_vk##N)get_device(device, "vk" #N))) return 10;
    DEVICE_FUNCTIONS(LOAD)
    VRS_FUNCTIONS(LOAD)
#undef LOAD
    VkQueue queue;
    p_GetDeviceQueue(device, family, 0, &queue);
    int doublewide = test_image(device, queue, family, 2880, 1440, 1);
    int array = test_image(device, queue, family, 1440, 1440, 2);
    fflush(0); sleep(2);
    p_DestroyDevice(device, 0); p_DestroyInstance(instance, 0);
    return doublewide && array ? 0 : 11;
}
