/* Minimal Linux framebuffer splash. The VT remains in process mode while any
 * pixels are written: the kernel cannot switch VTs until we acknowledge release. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <linux/vt.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define W 480
#define H 270
static uint32_t channel(unsigned v, struct fb_bitfield b) {
    if (!b.length || b.length > 8 || b.msb_right || b.offset + b.length > 32) return 0;
    return (v >> (8 - b.length)) << b.offset;
}
static int active(int fd) { struct vt_stat s; return ioctl(fd, VT_GETSTATE, &s) == 0 ? s.v_active : -1; }
int main(int argc, char **argv) {
    int tty = -1, fb = -1, graphics = 0, claimed = 0, activated = 0, owned = 0, rc = 1;
    unsigned char *pixels = NULL, *mem = MAP_FAILED;
    size_t length = 0; FILE *asset = NULL;
    struct fb_var_screeninfo v; struct fb_fix_screeninfo f;
    struct vt_mode mode = {0}, old = {0};
    sigset_t signals; sigemptyset(&signals); sigaddset(&signals, SIGUSR1);
    sigaddset(&signals, SIGTERM); sigaddset(&signals, SIGINT); sigaddset(&signals, SIGUSR2);
    if (argc == 3 && !strcmp(argv[1], "--recover")) {
        char token[32], expected[32];
        FILE *owner = fopen("/run/astro-graphic-splash.owner", "r");
        if (!owner) return 0;
        if (!fgets(token, sizeof token, owner)) { fclose(owner); return 1; }
        fclose(owner);
        if (snprintf(expected, sizeof expected, "%s\n", argv[2]) >= (int)sizeof expected || strcmp(token, expected)) return 1;
        tty = open("/dev/tty7", O_RDWR | O_CLOEXEC | O_NOCTTY);
        if (tty < 0) return 1;
        ioctl(tty, KDSETMODE, KD_TEXT);
        if (active(tty) == 7) ioctl(tty, VT_ACTIVATE, 1);
        close(tty); unlink("/run/astro-graphic-splash.owner"); return 0;
    }
    if ((argc != 2 && (argc != 3 || strcmp(argv[2], "--handoff"))) || sigprocmask(SIG_BLOCK, &signals, NULL)) goto out;
    asset = fopen(argv[1], "rb");
    if (!asset) goto out;
    char header[32];
    if (!fgets(header, sizeof header, asset) || strcmp(header, "P6\n") ||
        !fgets(header, sizeof header, asset) || strcmp(header, "480 270\n") ||
        !fgets(header, sizeof header, asset) || strcmp(header, "255\n")) goto out;
    pixels = malloc(W * H * 3);
    if (!pixels || fread(pixels, 1, W * H * 3, asset) != W * H * 3 || fgetc(asset) != EOF) goto out;
    tty = open("/dev/tty7", O_RDWR | O_CLOEXEC | O_NOCTTY);
    fb = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (tty < 0 || fb < 0 || active(tty) != 1 || ioctl(tty, VT_GETMODE, &old) || old.mode != VT_AUTO ||
        ioctl(fb, FBIOGET_FSCREENINFO, &f) || ioctl(fb, FBIOGET_VSCREENINFO, &v)) goto out;
    if (f.type != FB_TYPE_PACKED_PIXELS || f.visual != FB_VISUAL_TRUECOLOR ||
        (v.bits_per_pixel != 16 && v.bits_per_pixel != 32) || !v.xres || !v.yres ||
        v.xres > 8192 || v.yres > 8192 || v.xoffset || v.yoffset ||
        !v.red.length || !v.green.length || !v.blue.length ||
        v.red.length > 8 || v.green.length > 8 || v.blue.length > 8 ||
        v.red.msb_right || v.green.msb_right || v.blue.msb_right ||
        v.red.offset + v.red.length > v.bits_per_pixel || v.green.offset + v.green.length > v.bits_per_pixel ||
        v.blue.offset + v.blue.length > v.bits_per_pixel ||
        f.line_length < v.xres * (v.bits_per_pixel / 8) ||
        (uint64_t)f.line_length * v.yres > f.smem_len || f.smem_len > 256 * 1024 * 1024) goto out;
    length = f.smem_len;
    mem = mmap(NULL, length, PROT_READ | PROT_WRITE, MAP_SHARED, fb, 0);
    if (mem == MAP_FAILED) goto out;
    mode.mode = VT_PROCESS; mode.relsig = SIGUSR1; mode.acqsig = SIGUSR2;
    if (ioctl(tty, VT_SETMODE, &mode)) goto out;
    claimed = 1;
    int ownerfd = open("/run/astro-graphic-splash.owner", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (ownerfd < 0) goto out;
    char token[32]; int tokenlen = snprintf(token, sizeof token, "%ld\n", (long)getpid());
    owned = 1;
    if (write(ownerfd, token, tokenlen) != tokenlen) { close(ownerfd); goto out; }
    close(ownerfd);
    if (active(tty) != 1 || ioctl(tty, KDSETMODE, KD_GRAPHICS)) goto out;
    graphics = 1;
    if (active(tty) != 1 || ioctl(tty, VT_ACTIVATE, 7)) goto out;
    /* VT_WAITACTIVE can block forever if the current VT refuses release. */
    for (int i=0; i<100 && active(tty)!=7; i++) {
        if (active(tty) != 1) goto out;
        struct timespec pause = {0, 10000000}; nanosleep(&pause, NULL);
    }
    if (active(tty) != 7) goto out;
    activated = 1;
    /* Pending release is serviced before drawing. */
    struct timespec zero = {0, 0};
    int pending;
    while ((pending = sigtimedwait(&signals, NULL, &zero)) >= 0) {
        if (pending == SIGUSR2) { ioctl(tty, VT_RELDISP, VT_ACKACQ); continue; }
        if (pending == SIGUSR1) { ioctl(tty, KDSETMODE, KD_TEXT); graphics = 0; ioctl(tty, VT_RELDISP, 1); activated = 0; }
        goto out;
    }
    unsigned dw = W, dh = H;
    if (dw > v.xres || dh > v.yres) {
        if ((uint64_t)v.xres * H < (uint64_t)v.yres * W) { dw = v.xres; dh = (unsigned)((uint64_t)H * dw / W); }
        else { dh = v.yres; dw = (unsigned)((uint64_t)W * dh / H); }
    }
    if (!dw || !dh) goto out;
    memset(mem, 0, (size_t)f.line_length * v.yres);
    unsigned x0 = (v.xres - dw)/2, y0 = (v.yres - dh)/2, bytes = v.bits_per_pixel/8;
    for (unsigned y=0; y<dh; y++) for (unsigned x=0; x<dw; x++) {
        unsigned char *p = pixels + (((uint64_t)y*H/dh)*W + (uint64_t)x*W/dw)*3;
        uint32_t color = channel(p[0],v.red) | channel(p[1],v.green) | channel(p[2],v.blue);
        memcpy(mem + (size_t)(y+y0)*f.line_length + (size_t)(x+x0)*bytes, &color, bytes);
    }
    /* No repaint: a release signal means the operator chose another console. */
    struct timespec timeout = {180, 0};
    siginfo_t info; int sig;
    do { sig = sigtimedwait(&signals, &info, &timeout); if (sig == SIGUSR2) ioctl(tty, VT_RELDISP, VT_ACKACQ); } while (sig == SIGUSR2);
    if ((sig == SIGTERM || sig == SIGINT) && argc == 3) {
        /* Leave the text VT active for a welcome screen handoff. */
        activated = 0;
    }
    if (sig == SIGUSR1) {
        ioctl(tty, KDSETMODE, KD_TEXT); graphics = 0;
        ioctl(tty, VT_RELDISP, 1);
        activated = 0;
    }
    rc = 0;
out:
    /* A queued release takes precedence over returning to tty1. */
    if (claimed && activated) {
        struct timespec z = {0, 0}; int pending;
        while ((pending = sigtimedwait(&signals, NULL, &z)) >= 0) {
            if (pending == SIGUSR2) ioctl(tty, VT_RELDISP, VT_ACKACQ);
            if (pending == SIGUSR1) {
                if (graphics) ioctl(tty, KDSETMODE, KD_TEXT);
                graphics = 0; ioctl(tty, VT_RELDISP, 1); activated = 0;
            }
        }
    }
    if (graphics) ioctl(tty, KDSETMODE, KD_TEXT);
    if (claimed) ioctl(tty, VT_SETMODE, &old);
    if (owned) unlink("/run/astro-graphic-splash.owner");
    if (activated && active(tty) == 7) ioctl(tty, VT_ACTIVATE, 1);
    if (mem != MAP_FAILED) munmap(mem, length);
    if (fb >= 0) close(fb);
    if (tty >= 0) close(tty);
    if (asset) fclose(asset);
    free(pixels);
    return rc;
}
