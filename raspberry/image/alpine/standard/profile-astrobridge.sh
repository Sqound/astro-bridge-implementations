profile_astrobridge() {
    profile_rpi
    title="Astro Bridge"
    desc="Astro Bridge diskless image for Raspberry Pi Zero W"
    apkovl="genapkovl-astrobridge.sh"
    # Modloop signing is separate from APKINDEX trust. mkimage --hostkeys embeds
    # publisher keys plus this job's ephemeral APKINDEX PUBLIC key, never private
    # signing material or SSH host keys. Package signatures remain official.
    modloop_sign=no
    apks="$apks spdx-licenses-text python3 networkmanager networkmanager-cli networkmanager-wifi networkmanager-openrc \
      openssh openssh-server-common-openrc wpa_supplicant e2fsprogs-extra \
      chrony chrony-openrc dbus dbus-openrc hostapd dnsmasq avahi avahi-openrc \
      nftables conntrack-tools iw iproute2 wireless-regdb linux-firmware-brcm linux-firmware-rtw88 eudev eudev-openrc usb-modeswitch usb-modeswitch-udev \
      shadow sudo ca-certificates openssl py3-cryptography py3-http-ece"
}
