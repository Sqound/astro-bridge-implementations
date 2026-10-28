# Choose an installation

[Documentation](README.md) / Installation options

**All three options run the same Astro Bridge application. What changes is how it is installed and which system manages the Wi-Fi adapters.**

| Option | What you get | How to use it |
| --- | --- | --- |
| **Complete Raspberry Pi image** | Alpine Linux, Astro Bridge, drivers and system services, already configured together | Flash an SD card and boot a supported Raspberry Pi Zero W |
| **Core only** | The Astro Bridge application: web interface, API, equipment plugins and connection logic | Install it into an existing Python environment; add the Linux host integration for real Wi-Fi control |
| **Docker** | The Core application and its container dependencies | Run it with Docker on an existing computer; choose demonstration mode or the Linux networking integration |

## Complete Raspberry Pi system

The `.img.xz` file is a **complete operating-system image**, based on Alpine Linux. Astro Bridge starts automatically as a system service when the Raspberry Pi boots. You do not need to install the Core application separately.

Use the [native flasher](flasher.md) or Raspberry Pi Imager, then follow [Raspberry setup](raspberry.md). This image targets the original Raspberry Pi Zero W; other boards require their own implementation and hardware validation.

## Core on an existing system

The Core release installs the application in an isolated Python environment. It does not install an operating system or automatically configure Wi-Fi drivers, NetworkManager, hotspot services or startup at boot.

Use the [Core installer](https://github.com/Sqound/astro-bridge-core/blob/main/doc/getting-started.md) to try the application. For real networking on a dedicated Linux computer, use the [native Linux implementation](linux.md), which adds the required host integration and service configuration.

## Docker on an existing computer

Docker packages the application and its userspace dependencies. Containers use the Linux host's kernel and adapters. On macOS/Windows, that Linux host is a virtual machine.

| Docker mode | What works |
| --- | --- |
| **Demo** | Explore the interface with simulated equipment; no physical Wi-Fi changes |
| **Linux networking** | Manage real adapters on a compatible dedicated Linux host with the required services and permissions |

Follow the [Docker guide](docker.md). The installer can prepare dependencies and start the selected mode; it does not replace the computer's operating system.

### USB Wi-Fi on macOS and Windows

Plugging an adapter into the computer does not automatically expose it as a Wi-Fi radio inside the Linux VM or container. Real use requires USB forwarding to the VM, a compatible Linux driver/firmware, and the same host-service integration as a physical Linux installation.

Docker Desktop documents [USB/IP forwarding](https://docs.docker.com/desktop/features/usbip/), but does not guarantee every USB device. Astro Bridge currently supports the standard macOS/Windows Docker setup for **demo mode**; USB Wi-Fi forwarding is not configured or validated by its installer. Host networking alone does not provide radio control.
