/* Offscreen attachment VRS + secondary command buffer API/readback regression.
 * Clears are not fragment shaders: this does not measure shading reduction.
 * Frame-specific 8x8 attachment texels, single-sample color, SBS and array.
 */
#define FRAME_VRS_SECONDARY_PROBE
#include "vulkan_image_probe.c"
