# Serve an attached camera with INDI

On a supported Debian-family host or appropriately configured Linux container,
the owner can install an existing INDI server and ZWO ASI driver. The bridge
ships only its supervisor. Pi Zero W and Zero 2 W remain connections/telemetry
platforms. Pi 3B is the UVC/ASI guide-camera bench only; its INDI launcher is
disabled. Native INDI or a main camera targets Pi 4/5 or Docker on another
Linux host. Pi 4 is conditional on the Pi 3B bench outcome, and Pi 5 remains
outside the current camera startup gate.
Real package startup, USB capture, network isolation and applications remain
documented, not verified on hardware.

## Owner preparation

1. Check the OS release, architecture and package origin. On releases that
   provide them, inspect `apt-cache policy indi-bin indi-asi bubblewrap` and
   install them with `sudo apt-get install indi-bin indi-asi bubblewrap`. Verify
   `/usr/bin/indiserver`, `/usr/bin/indi_asi_ccd`, `/usr/bin/python3` and
   `/usr/bin/bwrap`. Review the distribution's ASI SDK dependency and license.
   The INDI driver uses its own distro-packaged libraries. The separate Alpaca
   SDK installation is outside its sandbox. [INDI package instructions](https://docs.indilib.org/indiserver/)
   and [Debian ASI driver source](https://sources.debian.org/src/indi-asi/2.2%2B20221225102500-1/README.md).
2. Create a dedicated unprivileged primary group and system account, both
   named `astro-indi`, with no login shell, home or additional groups
   (e.g. `adduser --system --group --no-create-home astro-indi`). Give its
   primary group read/write permission on the exact camera model. For example,
   for USB ID `03c3:120a`, an owner-managed rule in
   `/etc/udev/rules.d/99-astro-bridge-indi.rules` can contain:

   ```udev
   SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="03c3", ATTR{idProduct}=="120a", OWNER:="root", GROUP:="astro-indi", MODE:="0660"
   ```

   Choose the exact product listed in the current ZWO camera manifest. Reload
   rules and reconnect the camera when idle. Verify the resulting USB device
   node is root-owned, group `astro-indi`, mode 0660. The earlier Alpaca SDK
   rule installs root:root 0600. The platform refuses that mode for INDI.
   Root retains Alpaca access. In a container, prepare the permissions on the
   host and map the dedicated group ID consistently.
3. Confirm the merged-/usr Linux layout, unprivileged user namespaces, private
   PID/network namespaces, Linux pidfd support, bubblewrap's `--disable-userns` and `--as-pid-1`,
   access to the reviewed USB node and permission for the bridge to bind
   management sockets to their interface. Container seccomp, AppArmor, device
   cgroups and kernel policy can prevent launch. Keep the option off if the
   deployment cannot provide isolation. The implementation adds no images,
   capabilities or container privileges.
4. Connect one supported ASI and disconnect it in any current Alpaca app.
   In **Configuration → Cameras**, enable **Serve with INDI too** and save.
   The master camera switch must also be on. Open the camera endpoint from
   your INDI application (for example KStars/Ekos). The bridge supplies the
   host and TCP port. Other supported camera families retain Alpaca.

## Confinement and supervision

Upstream's TCP listener binds a wildcard address, and its Linux Unix socket
uses the abstract namespace. Both stay inside a new network namespace with
loopback only. The host side passes confirmed management listeners bound to
both IPv4 address and interface into the confined worker. Allowed peers must
also fall within the configured management subnets. Route changes, owner
changes, uncertain scans, unplug, failed health and process exit withdraw
availability. The bridge never starts an unconfined fallback.
[Upstream TCP implementation](https://github.com/indilib/indi/blob/master/indiserver/TcpServer.cpp)
and [Unix implementation](https://github.com/indilib/indi/blob/master/indiserver/UnixServer.cpp).

The only allowed INDI command is `/usr/bin/indiserver -p 7624 indi_asi_ccd`.
The ASI driver enumerates a family of devices. The supervisor requires exactly
one ASI-vendor USB device and a reviewed exact descriptor, checks its current
bus/port/product and major/minor, and exposes only that one USB node in the
private /dev. Its corresponding USB sysfs subtree is the only host device
metadata mounted in the sandbox. Another ASI or unknown same-vendor device withdraws INDI.
A dedicated non-root account runs the server with no capabilities. The private
PID namespace ends when its worker exits; a still-running process prevents a
handoff. Before driver launch, the worker verifies it is PID 1 with only loopback
and the intended USB node, then passes its pidfd to the host. The bridge requires
both monitor exit and this independent namespace-lifetime signal before handoff.
Up to eight clients can reach the attached-camera service from allowed
management peers. The sandbox gives the driver no writable image/configuration directory. Clients
receive images over INDI. The helper discards process output, probes with bounded
`getProperties` and never stores a frame or a raw property.
[Upstream ASI implementation](https://github.com/indilib/indi-3rdparty/blob/master/indi-asi/asi_ccd.cpp).
