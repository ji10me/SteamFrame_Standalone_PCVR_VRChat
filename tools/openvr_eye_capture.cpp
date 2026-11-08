// Read one compositor mirror eye from a background client. No scene submission,
// tracking-space changes, input events or compositor fade changes.
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glx.h>
#include <X11/Xlib.h>
#include <openvr.h>

#include <algorithm>
#include <cstdio>
#include <cstdint>
#include <vector>
#include <sys/stat.h>

int main(int argc, char** argv) {
    umask(0077);
    if (argc != 2) {
        std::fprintf(stderr, "Usage: openvr_eye_capture PRIVATE_OUTPUT.ppm\n");
        return 2;
    }
    Display* display = XOpenDisplay(nullptr);
    if (!display) {
        std::fprintf(stderr, "X display unavailable\n");
        return 3;
    }
    int count = 0;
    const int attributes[] = {GLX_DRAWABLE_TYPE, GLX_PBUFFER_BIT, GLX_RENDER_TYPE, GLX_RGBA_BIT,
                              GLX_RED_SIZE, 8, GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8, None};
    GLXFBConfig* configs = glXChooseFBConfig(display, DefaultScreen(display), attributes, &count);
    if (!configs || count == 0) {
        std::fprintf(stderr, "GLX pbuffer configuration unavailable\n");
        if (configs) XFree(configs);
        XCloseDisplay(display);
        return 4;
    }
    const int pbuffer_attributes[] = {GLX_PBUFFER_WIDTH, 1, GLX_PBUFFER_HEIGHT, 1, None};
    GLXPbuffer buffer = glXCreatePbuffer(display, configs[0], pbuffer_attributes);
    GLXContext context = glXCreateNewContext(display, configs[0], GLX_RGBA_TYPE, nullptr, True);
    XFree(configs);
    if (!buffer || !context || !glXMakeContextCurrent(display, buffer, buffer, context)) {
        std::fprintf(stderr, "GLX context unavailable\n");
        if (context) glXDestroyContext(display, context);
        if (buffer) glXDestroyPbuffer(display, buffer);
        XCloseDisplay(display);
        return 5;
    }
    vr::EVRInitError init = vr::VRInitError_None;
    vr::VR_Init(&init, vr::VRApplication_Background);
    int result = 0;
    if (init != vr::VRInitError_None || !vr::VRCompositor()) {
        std::fprintf(stderr, "OpenVR background init failed: %d\n", static_cast<int>(init));
        result = 6;
    } else {
        auto* compositor = vr::VRCompositor();
        vr::glUInt_t texture = 0;
        vr::glSharedTextureHandle_t shared = nullptr;
        auto error = compositor->GetMirrorTextureGL(vr::Eye_Left, &texture, &shared);
        if (error != vr::VRCompositorError_None) {
            std::fprintf(stderr, "Compositor mirror unavailable: %d\n", static_cast<int>(error));
            result = 7;
        } else {
            compositor->LockGLSharedTextureForAccess(shared);
            glBindTexture(GL_TEXTURE_2D, texture);
            GLint width = 0, height = 0;
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &width);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &height);
            if (width <= 0 || height <= 0 || width > 8192 || height > 8192) {
                std::fprintf(stderr, "Unexpected eye texture dimensions\n");
                result = 8;
            } else {
                std::vector<uint8_t> rgba(static_cast<size_t>(width) * height * 4);
                glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
                if (glGetError() != GL_NO_ERROR) {
                    std::fprintf(stderr, "Eye readback failed\n");
                    result = 9;
                } else {
                    FILE* output = std::fopen(argv[1], "wbx");
                    if (!output) {
                        std::fprintf(stderr, "Cannot create a new private output file\n");
                        result = 10;
                    } else {
                        bool write_ok = std::fprintf(output, "P6\n%d %d\n255\n", width, height) > 0;
                        size_t nonblack = 0;
                        std::vector<uint8_t> row(static_cast<size_t>(width) * 3);
                        for (int y = height - 1; y >= 0; --y) {
                            for (int x = 0; x < width; ++x) {
                                size_t offset = (static_cast<size_t>(y) * width + x) * 4;
                                std::copy_n(rgba.data() + offset, 3, row.data() + static_cast<size_t>(x) * 3);
                                nonblack += rgba[offset] > 4 || rgba[offset + 1] > 4 || rgba[offset + 2] > 4;
                            }
                            write_ok &= std::fwrite(row.data(), 1, row.size(), output) == row.size();
                        }
                        write_ok &= std::fclose(output) == 0;
                        if (!write_ok) {
                            std::fprintf(stderr, "Eye image write failed\n");
                            result = 11;
                        } else {
                            std::printf("{\"width\":%d,\"height\":%d,\"nonblack_pixels\":%zu,\"total_pixels\":%zu}\n",
                                        width, height, nonblack, static_cast<size_t>(width) * height);
                        }
                    }
                }
            }
            glBindTexture(GL_TEXTURE_2D, 0);
            compositor->UnlockGLSharedTextureForAccess(shared);
            compositor->ReleaseSharedGLTexture(texture, shared);
        }
    }
    if (init == vr::VRInitError_None) vr::VR_Shutdown();
    glXMakeContextCurrent(display, None, None, nullptr);
    glXDestroyContext(display, context);
    glXDestroyPbuffer(display, buffer);
    XCloseDisplay(display);
    return result;
}
