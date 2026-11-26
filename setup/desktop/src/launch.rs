use mdns_sd::{ServiceDaemon, ServiceEvent};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket},
    sync::{Mutex, MutexGuard},
    time::{Duration, Instant},
};

const SERVICE: &str = "_astrobridge._tcp.local.";
const HOTSPOT: Ipv4Addr = Ipv4Addr::new(192, 168, 77, 1);
const LIMIT: usize = 65536;
/// Status, devices and cameras can be long on a busy bridge.
const BIG: usize = 1 << 20;
/// A bridge found by the search stays listed while it misses fewer rounds than this.
const MISSES: u8 = 3;
/// Bridge tokens last 12 hours (token_expires_in 43200); stop using one a little before.
const SESSION_TTL: Duration = Duration::from_secs(43200 - 300);
/// The session is gone (expired, forgotten or refused by the bridge): ask the password again.
pub const SESSION_EXPIRED: &str = "SESSION_EXPIRED";
/// The bridge web interface is needed to add this mount (network password or address unknown).
pub const USE_ADMIN_INTERFACE: &str = "USE_ADMIN_INTERFACE";

#[derive(Clone)]
struct Endpoint {
    host: String,
    ip: Ipv4Addr,
    port: u16,
    secure: bool,
    hotspot: bool,
    legacy: bool,
    identity: Identity,
    public: Public,
}
/// What the public /api/v1/info says besides the identity.
#[derive(Clone, Debug, Default)]
struct Public {
    hotspot: Option<bool>,
    addresses: Vec<Address>,
}
/// One web interface address of the bridge (info.management_endpoints), given by the bridge.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Address {
    path: String,
    host: String,
    url: String,
    tls: bool,
}
#[derive(Clone, Debug)]
struct Identity {
    id: String,
    name: String,
    version: String,
    path: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Bridge {
    /// The device_id of the bridge: the same from one search to the next.
    id: String,
    name: String,
    version: String,
    path: String,
    auth_mode: String,
    ssh_host: String,
    /// info.hotspot, when the bridge says it.
    hotspot_active: Option<bool>,
    addresses: Vec<Address>,
    /// The web interface address to open, as given by the bridge.
    admin_url: String,
    /// Added by its IP address: stays listed between search rounds.
    manual: bool,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeDetails {
    home_ssid: String,
    home_connected: bool,
    hotspot_ssid: String,
    hotspot_active: bool,
    /// The Linux account SSH accepts (status.ssh.account), when the bridge says it.
    ssh_account: Option<String>,
    interfaces: Option<Interfaces>,
    /// private, hotspot_isolated or hotspot_shared.
    connection_mode: Option<String>,
    hotspot_subnet: Option<String>,
    found: Vec<FoundDevice>,
    cameras: Vec<Camera>,
    added: Vec<AddedDevice>,
}
#[derive(Debug, PartialEq, Serialize)]
pub struct Interfaces {
    total: u8,
    builtin: u8,
    usb: u8,
}
#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FoundDevice {
    id: String,
    name: String,
    /// mount, telescope or other.
    kind: String,
    /// wifi or serial_usb.
    link: String,
    signal: Option<u8>,
    /// Only mounts can be added from Setup.
    can_add: bool,
}
#[derive(Debug, PartialEq, Serialize)]
pub struct Camera {
    name: String,
    /// detected, preparing, ready, driver_missing or unsupported.
    state: String,
}
#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AddedDevice {
    name: String,
    /// mount, smart_telescope or other.
    kind: String,
    /// wifi, serial_usb or usb.
    link_type: String,
    /// online, offline or unknown.
    state: String,
    port: Option<u16>,
}
/// root on the Pi 5, bridge on the Pi Zero W: a plain Linux account name, nothing else.
fn ssh_account(value: &Value) -> Option<String> {
    let name = value.pointer("/ssh/account")?.as_str()?;
    let bytes = name.as_bytes();
    (!bytes.is_empty()
        && bytes.len() <= 32
        && bytes[0].is_ascii_lowercase()
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_' || *b == b'-'))
    .then(|| name.to_owned())
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChangeResult {
    sessions_invalidated: bool,
    hotspot_reconnect: bool,
    /// Setup signed in again with the new password: details keep refreshing.
    session_kept: bool,
}
struct Entry {
    endpoint: Endpoint,
    manual: bool,
    missed: u8,
}
/// Something the bridge offers to add, kept here so the window only names it.
#[derive(Clone, Debug, PartialEq)]
enum Addable {
    Wifi(String),
    Serial(Value),
}
/// The bridge token, only in memory, never the password.
struct Session {
    id: String,
    /// Where the token was obtained (trusted HTTPS or a confirmed direct hotspot); only used there.
    endpoint: Endpoint,
    token: String,
    expires: Instant,
    categories: HashMap<String, String>,
    addable: HashMap<String, Addable>,
}
#[derive(Default)]
pub struct Launch {
    entries: HashMap<String, Entry>,
    session: Option<Session>,
    /// Sessions dropped under the lock, signed out once it is released.
    ending: Vec<Session>,
    /// Raised by forget: a sign-in that started before it is not kept.
    epoch: u64,
}
/// Bridges verified by one search round.
pub struct Scan(Vec<Endpoint>);

fn local(ip: Ipv4Addr) -> bool {
    if !ip.is_private() && !ip.is_link_local() {
        return false;
    }
    if_addrs::get_if_addrs()
        .unwrap_or_default()
        .iter()
        .any(|i| match i.addr {
            if_addrs::IfAddr::V4(ref a) if !a.ip.is_loopback() && !a.ip.is_unspecified() => {
                let mask = u32::from(a.netmask);
                let target = u32::from(ip);
                let own = u32::from(a.ip);
                mask.count_ones() >= 16
                    && target & mask == own & mask
                    && target != own
                    && target != (own & mask)
                    && target != (own | !mask)
            }
            _ => false,
        })
}
fn direct_hotspot() -> bool {
    if !local(HOTSPOT) {
        return false;
    }
    let Ok(s) = UdpSocket::bind("0.0.0.0:0") else {
        return false;
    };
    if s.connect((HOTSPOT, 80)).is_err() {
        return false;
    }
    let Ok(SocketAddr::V4(addr)) = s.local_addr() else {
        return false;
    };
    if addr.ip().octets()[0..3] != [192, 168, 77] || *addr.ip() == HOTSPOT {
        return false;
    }
    let interfaces = if_addrs::get_if_addrs().unwrap_or_default();
    let Some(iface) = interfaces.iter().find(|i| matches!(i.addr, if_addrs::IfAddr::V4(ref a) if a.ip == *addr.ip() && a.netmask == Ipv4Addr::new(255,255,255,0))) else { return false };
    #[cfg(target_os = "macos")]
    {
        let Ok(out) = std::process::Command::new("/sbin/route")
            .args(["-n", "get", "192.168.77.1"])
            .output()
        else {
            return false;
        };
        if !out.status.success() {
            return false;
        };
        return mac_direct_route(&String::from_utf8_lossy(&out.stdout), &iface.name);
    }
    #[cfg(target_os = "linux")]
    {
        let Ok(out) = std::process::Command::new("/usr/sbin/ip")
            .args(["-4", "route", "get", "192.168.77.1"])
            .output()
        else {
            return false;
        };
        if !out.status.success() {
            return false;
        };
        let text = String::from_utf8_lossy(&out.stdout);
        let words: Vec<_> = text.split_whitespace().collect();
        return !words.contains(&"via")
            && words.windows(2).any(|w| w == ["dev", iface.name.as_str()]);
    }
    #[cfg(target_os = "windows")]
    {
        // Fixed command and fixed destination. Only the OS-selected source address is supplied.
        let Some(root) = std::env::var_os("SystemRoot") else {
            return false;
        };
        let exe =
            std::path::PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let script = "$ErrorActionPreference='Stop'; $r=@(Find-NetRoute -RemoteIPAddress '192.168.77.1' | Where-Object {$_.DestinationPrefix}); $a=@(Get-NetIPAddress -IPAddress $env:ASTRO_SETUP_SOURCE -AddressFamily IPv4); if ($r.Count -eq 1 -and $a.Count -eq 1 -and $r[0].InterfaceIndex -eq $a[0].InterfaceIndex -and $a[0].PrefixLength -eq 24 -and $r[0].NextHop -eq '0.0.0.0') { exit 0 }; exit 1";
        return std::process::Command::new(exe)
            .args(["-NoProfile", "-NonInteractive", "-Command", script])
            .env("ASTRO_SETUP_SOURCE", addr.ip().to_string())
            .output()
            .is_ok_and(|o| o.status.success());
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        let _ = iface;
        false
    }
}
#[cfg(any(target_os = "macos", test))]
fn mac_direct_route(text: &str, interface: &str) -> bool {
    let field = |key: &str| {
        text.lines()
            .find_map(|l| l.trim().strip_prefix(key).map(str::trim))
    };
    let Some(flags) = field("flags:") else {
        return false;
    };
    let direct_gateway = field("gateway:").is_none_or(|g| {
        g.starts_with("link#")
            || (flags.contains("LLINFO")
                && g.split(':').count() == 6
                && g.split(':').all(|part| {
                    !part.is_empty()
                        && part.len() <= 2
                        && part.bytes().all(|b| b.is_ascii_hexdigit())
                }))
    });
    field("interface:") == Some(interface) && !flags.contains("GATEWAY") && direct_gateway
}
fn preference(e: &Endpoint) -> (u8, Ipv4Addr, u16, &str) {
    (
        if e.secure {
            0
        } else if e.hotspot {
            1
        } else {
            2
        },
        e.ip,
        e.port,
        &e.host,
    )
}
fn safe_host(host: &str) -> bool {
    let h = host.strip_suffix('.').unwrap_or(host);
    h.len() <= 253
        && h.ends_with(".local")
        && h.split('.').all(|p| {
            !p.is_empty()
                && p.len() <= 63
                && !p.starts_with('-')
                && !p.ends_with('-')
                && p.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
        })
}
fn valid_id(id: &str) -> bool {
    id.len() == 16
        && id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn identity(v: &Value) -> Result<Identity, String> {
    let s = |k| {
        v.get(k)
            .and_then(Value::as_str)
            .ok_or_else(|| "Invalid bridge identity".to_string())
    };
    let id = s("device_id")?;
    let name = s("name")?;
    let version = s("version")?;
    let path = s("request_path")?;
    if v.get("product").and_then(Value::as_str) != Some("astro-bridge")
        || v.get("api_version").and_then(Value::as_str) != Some("1")
        || v.get("contract_version").and_then(Value::as_u64) != Some(1)
        || !valid_id(id)
        || name.is_empty()
        || name.len() > 63
        || version.is_empty()
        || version.len() > 128
        || !matches!(path, "home" | "hotspot" | "other")
    {
        return Err("Invalid bridge identity".into());
    }
    Ok(Identity {
        id: id.into(),
        name: name.into(),
        version: version.into(),
        path: path.into(),
    })
}
fn manual_address(value: &str) -> Result<Ipv4Addr, String> {
    let ip: Ipv4Addr = value.parse().map_err(|_| "Invalid IPv4 address")?;
    if value != ip.to_string() || (!ip.is_private() && !ip.is_link_local()) {
        return Err("Invalid local IPv4 address".into());
    }
    Ok(ip)
}
fn manual_identity(value: &Value) -> Result<Identity, String> {
    let found = identity(value)?;
    if found.path != "home" {
        return Err("Bridge is not on its home network".into());
    }
    Ok(found)
}
// Version 1.0.1 has no stable identity or ingress metadata. Only recognize the
// exact public response on an explicitly confirmed, directly routed hotspot.
fn legacy_identity(v: &Value) -> Option<Identity> {
    const CAPS: &[&str] = &[
        "telescope_profiles",
        "radio_switching",
        "home_wifi",
        "wifi_scan",
        "interface_inventory",
        "hotspot",
        "settings",
        "passwords",
        "reboot",
        "connection_events",
        "safe_discovery",
        "physical_binding",
        "profile_ensure",
        "connection_operations",
        "explicit_auto_policy",
        "connection_activity",
        "client_reservations",
        "owner_locks",
        "quiet_replacement",
        "advanced_interruption_policy",
        "seestar_demand",
        "vespera_connection_demand",
        "dwarf_discovery_demand",
        "dwarf_native_proxy",
        "dwarf_passive_demand",
        "durable_connection_pauses",
        "field_management_paths",
        "hotspot_internet_sharing",
        "consultation_metadata",
        "wifi_mount_profiles",
        "zwo_mount_native_proxy",
        "synscan_udp_proxy",
        "declarative_plugins",
        "mount_read_only_identification",
        "zwo_mount_connection_demand",
        "synscan_discovery_demand",
    ];
    if v.as_object().is_none_or(|o| o.len() != 7)
        || ["contract_version", "device_id", "name", "request_path"]
            .iter()
            .any(|k| v.get(k).is_some())
        || v.get("product")?.as_str()? != "astro-bridge"
        || v.get("version")?.as_str()? != "1.0.1"
        || v.get("api_version")?.as_str()? != "1"
        || v.get("authentication")?.as_str()? != "bearer"
        || v.get("token_expires_in")?.as_u64()? != 43200
        || v.get("openapi_url")?.as_str()? != "/api/v1/openapi.json"
    {
        return None;
    }
    let actual: Option<Vec<&str>> = v
        .get("capabilities")?
        .as_array()?
        .iter()
        .map(Value::as_str)
        .collect();
    if actual? != CAPS {
        return None;
    }
    Some(Identity {
        id: "legacy-hotspot".into(),
        name: "Astro Bridge 1.0.1".into(),
        version: "1.0.1".into(),
        path: "other".into(),
    })
}
fn client(e: &Endpoint) -> Result<reqwest::blocking::Client, String> {
    if !local(e.ip) || (e.hotspot && !direct_hotspot()) {
        return Err("Bridge is no longer on an approved local route".into());
    }
    reqwest::blocking::Client::builder()
        .no_proxy()
        .http1_only()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(3))
        .connect_timeout(Duration::from_secs(2))
        .resolve(&e.host, SocketAddr::new(IpAddr::V4(e.ip), e.port))
        .build()
        .map_err(|_| "Cannot create local connection".into())
}
fn origin(e: &Endpoint) -> String {
    format!(
        "{}://{}:{}",
        if e.secure { "https" } else { "http" },
        e.host,
        e.port
    )
}
/// A bridge web interface address: http or https, a local IPv4 address or a .local name, path /.
fn web_url(value: &str) -> Option<reqwest::Url> {
    if value.len() > 256 {
        return None;
    }
    let url = reqwest::Url::parse(value).ok()?;
    if url.as_str() != value
        || !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        return None;
    }
    let host = url.host_str()?;
    let allowed = match host.parse::<Ipv4Addr>() {
        Ok(ip) => ip.is_private() || ip.is_link_local(),
        Err(_) => safe_host(host),
    };
    allowed.then_some(url)
}
fn public_info(v: &Value) -> Public {
    let addresses = v
        .get("management_endpoints")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .take(8)
                .filter_map(|item| {
                    let url = item.get("url")?.as_str()?;
                    let path = item.get("path")?.as_str()?;
                    let tls = item.get("tls")?.as_bool()?;
                    let parsed = web_url(url)?;
                    if !matches!(path, "home" | "hotspot") || tls != (parsed.scheme() == "https") {
                        return None;
                    }
                    Some(Address {
                        path: path.into(),
                        host: parsed.host_str()?.to_string(),
                        url: url.into(),
                        tls,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    Public {
        hotspot: v.get("hotspot").and_then(Value::as_bool),
        addresses,
    }
}
/// The address the bridge gives for the path Setup reaches it on; older bridges give none.
/// Only an address on the host Setup verified is opened, never another one named by the reply.
fn admin_url(e: &Endpoint) -> String {
    e.public
        .addresses
        .iter()
        .filter(|a| a.host == e.host || a.host == e.ip.to_string())
        .min_by_key(|a| (a.tls != e.secure, a.host != e.host))
        .map(|a| a.url.clone())
        .unwrap_or_else(|| {
            let default = if e.secure { 443 } else { 80 };
            let port = if e.port == default {
                String::new()
            } else {
                format!(":{}", e.port)
            };
            format!(
                "{}://{}{port}/",
                if e.secure { "https" } else { "http" },
                e.host
            )
        })
}
fn request(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    method: reqwest::Method,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> Result<Value, String> {
    request_limited(c, e, method, path, token, body, LIMIT)
}
fn request_limited(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    method: reqwest::Method,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
    limit: usize,
) -> Result<Value, String> {
    request_checked(c, e, method, path, token, body, limit, None)
}
/// Same as request_limited, with the configuration revision the change applies to (If-Match).
#[allow(clippy::too_many_arguments)]
fn request_checked(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    method: reqwest::Method,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
    limit: usize,
    revision: Option<&str>,
) -> Result<Value, String> {
    let mut req = c.request(method, format!("{}{path}", origin(e)));
    if let Some(t) = token {
        req = req.bearer_auth(t);
    }
    if let Some(r) = revision {
        req = req.header("If-Match", format!("\"{r}\""));
    }
    if let Some(b) = body {
        req = req.json(&b);
    }
    let response = req
        .send()
        .map_err(|_| "Bridge connection failed".to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "Bridge returned HTTP {}",
            response.status().as_u16()
        ));
    }
    if response.content_length().is_some_and(|n| n > limit as u64) {
        return Err("Bridge response too large".into());
    }
    let mut bytes = Vec::new();
    std::io::Read::take(response, limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Cannot read bridge response".to_string())?;
    if bytes.len() > limit {
        return Err("Bridge response too large".into());
    }
    serde_json::from_slice(&bytes).map_err(|_| "Invalid bridge response".into())
}
use std::io::Read;
fn verify(c: &reqwest::blocking::Client, e: &Endpoint) -> Result<(), String> {
    let value = request(c, e, reqwest::Method::GET, "/api/v1/info", None, None)?;
    let found = if e.legacy {
        legacy_identity(&value).ok_or("Legacy bridge identity changed")?
    } else {
        identity(&value)?
    };
    if found.id != e.identity.id
        || found.version != e.identity.version
        || found.name != e.identity.name
        || found.path != e.identity.path
    {
        return Err("Bridge identity changed; discover again".into());
    }
    Ok(())
}
fn probe_manual(c: &reqwest::blocking::Client, e: &mut Endpoint) -> Result<(), String> {
    let value = request(c, e, reqwest::Method::GET, "/api/v1/info", None, None)?;
    e.identity = manual_identity(&value)?;
    e.public = public_info(&value);
    Ok(())
}
fn auth(e: &Endpoint, password: &str) -> Result<(reqwest::blocking::Client, String), String> {
    if e.legacy {
        return Err("This legacy bridge supports browser access only".into());
    }
    // Plain HTTP is accepted on the local network, like the bridge web interface,
    // so no certificate step is ever needed. client() still requires a private address on
    // a network of this computer, and the session stays on this endpoint only. A hotspot
    // address still needs the confirmed direct hotspot.
    if !e.secure && e.hotspot && !(e.identity.path == "hotspot" && direct_hotspot()) {
        return Err("A confirmed direct hotspot is required".into());
    }
    let c = client(e)?;
    let token = login(&c, e, password)?;
    Ok((c, token))
}
fn login(c: &reqwest::blocking::Client, e: &Endpoint, password: &str) -> Result<String, String> {
    verify(c, e)?;
    let v = request(
        c,
        e,
        reqwest::Method::POST,
        "/api/v1/auth/token",
        None,
        Some(json!({"password":password})),
    )?;
    let token = v
        .get("access_token")
        .and_then(Value::as_str)
        .filter(|t| !t.is_empty() && t.len() <= 512)
        .ok_or("Invalid login response")?;
    if v.get("token_type").and_then(Value::as_str) != Some("Bearer") {
        return Err("Invalid login response".into());
    }
    Ok(token.into())
}
fn revoke(c: &reqwest::blocking::Client, e: &Endpoint, t: &str) {
    let _ = request(
        c,
        e,
        reqwest::Method::DELETE,
        "/api/v1/auth/token",
        Some(t),
        Some(json!({})),
    );
}
/// One search round: mDNS announcements (and the joined hotspot), each verified by /api/v1/info.
/// Runs without the Launch lock, so the window keeps working while it searches.
pub fn scan(joined: bool) -> Scan {
    let mut candidates = Vec::new();
    if let Ok(daemon) = ServiceDaemon::new() {
        if let Ok(receiver) = daemon.browse(SERVICE) {
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline && candidates.len() < 12 {
                let Ok(event) =
                    receiver.recv_timeout(deadline.saturating_duration_since(Instant::now()))
                else {
                    break;
                };
                if let ServiceEvent::ServiceResolved(info) = event {
                    let prop = |k| info.get_property_val_str(k);
                    let (Some(id), Some(name), Some(version), Some(caps)) =
                        (prop("id"), prop("name"), prop("version"), prop("caps"))
                    else {
                        continue;
                    };
                    if prop("product") != Some("astro-bridge")
                        || prop("api") != Some("/api/v1")
                        || prop("api-version") != Some("1")
                        || prop("contract") != Some("1")
                        || !valid_id(id)
                        || name.is_empty()
                        || name.len() > 63
                        || version.is_empty()
                        || info.get_port() != 80
                        || !safe_host(info.get_hostname())
                    {
                        continue;
                    }
                    for ip in info
                        .get_addresses()
                        .iter()
                        .filter_map(|a| {
                            if let IpAddr::V4(ip) = a {
                                Some(*ip)
                            } else {
                                None
                            }
                        })
                        .filter(|ip| local(*ip))
                        .take(2)
                    {
                        let base = Endpoint {
                            host: ip.to_string(),
                            ip,
                            port: 80,
                            secure: false,
                            hotspot: false,
                            legacy: false,
                            identity: Identity {
                                id: id.into(),
                                name: name.into(),
                                version: version.into(),
                                path: String::new(),
                            },
                            public: Public::default(),
                        };
                        candidates.push((
                            base,
                            prop("https")
                                .and_then(|s| s.parse::<u16>().ok())
                                .filter(|p| *p != 0),
                            info.get_hostname().trim_end_matches('.').to_string(),
                            Some(caps.to_string()),
                        ));
                        if candidates.len() >= 12 {
                            break;
                        }
                    }
                }
            }
            let _ = daemon.stop_browse(SERVICE);
        }
        let _ = daemon.shutdown();
    }
    if joined && direct_hotspot() {
        candidates.push((
            Endpoint {
                host: HOTSPOT.to_string(),
                ip: HOTSPOT,
                port: 80,
                secure: false,
                hotspot: true,
                legacy: false,
                identity: Identity {
                    id: String::new(),
                    name: String::new(),
                    version: String::new(),
                    path: String::new(),
                },
                public: Public::default(),
            },
            None,
            String::new(),
            None,
        ));
    }
    // Probe the explicitly confirmed hotspot first; stop starting probes after the deadline.
    candidates.sort_by(|a, b| (!a.0.hotspot, a.0.ip, &a.2).cmp(&(!b.0.hotspot, b.0.ip, &b.2)));
    let deadline = Instant::now() + Duration::from_secs(8);
    let mut verified: Vec<Endpoint> = Vec::new();
    for (mut e, https, host, caps) in candidates {
        if Instant::now() >= deadline {
            break;
        }
        let Ok(c) = client(&e) else { continue };
        let Ok(v) = request(&c, &e, reqwest::Method::GET, "/api/v1/info", None, None) else {
            continue;
        };
        let found = match identity(&v) {
            Ok(found) => found,
            Err(_) if e.hotspot => match legacy_identity(&v) {
                Some(found) => {
                    e.legacy = true;
                    found
                }
                None => continue,
            },
            Err(_) => continue,
        };
        if e.hotspot {
            if !e.legacy && found.path != "hotspot" {
                continue;
            }
        } else if found.id != e.identity.id
            || found.name != e.identity.name
            || found.version != e.identity.version
        {
            continue;
        }
        if let Some(caps) = caps {
            let Some(actual) = v.get("capabilities").and_then(Value::as_array) else {
                continue;
            };
            let mut advertised: Vec<&str> = if caps.is_empty() {
                vec![]
            } else {
                caps.split(',').collect()
            };
            let Some(mut actual): Option<Vec<&str>> = actual.iter().map(Value::as_str).collect()
            else {
                continue;
            };
            advertised.sort_unstable();
            actual.sort_unstable();
            if advertised != actual {
                continue;
            }
        }
        e.identity = found;
        e.public = public_info(&v);
        if let Some(port) = https.filter(|_| Instant::now() < deadline) {
            let mut tls = e.clone();
            tls.host = host;
            tls.port = port;
            tls.secure = true;
            if let Ok(c) = client(&tls) {
                if verify(&c, &tls).is_ok() {
                    e = tls;
                }
            }
        }
        if !verified
            .iter()
            .any(|old| old.identity.id == e.identity.id && same_address(old, &e))
        {
            verified.push(e);
        }
    }
    Scan(verified)
}
fn lock(launch: &Mutex<Launch>) -> MutexGuard<'_, Launch> {
    launch
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}
fn entry_bridge(entry: &Entry) -> Bridge {
    let e = &entry.endpoint;
    let auth_mode = if e.legacy {
        "none"
    } else if e.secure {
        "https"
    } else if e.hotspot {
        "hotspot"
    } else {
        "http"
    };
    Bridge {
        id: e.identity.id.clone(),
        name: e.identity.name.clone(),
        version: e.identity.version.clone(),
        path: e.identity.path.clone(),
        auth_mode: auth_mode.into(),
        ssh_host: e.ip.to_string(),
        hotspot_active: e.public.hotspot,
        addresses: e.public.addresses.clone(),
        admin_url: admin_url(e),
        manual: entry.manual,
    }
}
impl Launch {
    /// Merge a search round. Identifiers are the device_id of each bridge, so a chosen bridge stays
    /// chosen; a bridge added by address stays listed, a found one after missing MISSES rounds goes.
    pub fn merge(&mut self, scan: Scan) -> Vec<Bridge> {
        let mut found: HashMap<String, Vec<Endpoint>> = HashMap::new();
        for e in scan.0 {
            found.entry(e.identity.id.clone()).or_default().push(e);
        }
        for (id, entry) in self.entries.iter_mut() {
            if !found.contains_key(id) {
                entry.missed = entry.missed.saturating_add(1);
            }
        }
        self.entries
            .retain(|_, entry| entry.manual || entry.missed < MISSES);
        for (id, candidates) in found {
            if self.entries.contains_key(&id) {
                let locked = self.session.as_ref().is_some_and(|s| s.id == id);
                let entry = self.entries.get_mut(&id).unwrap();
                entry.missed = 0;
                if let Some(next) = choose(&entry.endpoint, &candidates, entry.manual || locked) {
                    let moved = !same_address(&next, &entry.endpoint);
                    entry.endpoint = next;
                    if moved {
                        self.end_session(&id);
                    }
                }
            } else if self.entries.len() < 64 {
                if let Some(best) = candidates.iter().min_by_key(|e| preference(e)) {
                    self.entries.insert(
                        id,
                        Entry {
                            endpoint: best.clone(),
                            manual: false,
                            missed: 0,
                        },
                    );
                }
            }
        }
        if let Some(id) = self
            .session
            .as_ref()
            .filter(|s| !self.entries.contains_key(&s.id))
            .map(|s| s.id.clone())
        {
            self.end_session(&id);
        }
        self.bridges()
    }
    /// Drop the session of this bridge; it is signed out once the lock is released.
    fn end_session(&mut self, id: &str) {
        if self.session.as_ref().is_some_and(|s| s.id == id) {
            self.ending.extend(self.session.take());
        }
    }
    pub fn bridges(&self) -> Vec<Bridge> {
        let mut entries: Vec<&Entry> = self.entries.values().collect();
        entries.sort_by(|a, b| {
            (a.manual, &a.endpoint.identity.name, &a.endpoint.identity.id).cmp(&(
                b.manual,
                &b.endpoint.identity.name,
                &b.endpoint.identity.id,
            ))
        });
        entries.into_iter().map(entry_bridge).collect()
    }
    fn insert_manual(&mut self, e: Endpoint) -> Result<Bridge, String> {
        let id = e.identity.id.clone();
        if !self.entries.contains_key(&id) && self.entries.len() >= 64 {
            return Err("Too many bridges; search again".into());
        }
        // Another bridge added earlier at this address has left it.
        self.entries
            .retain(|key, old| key == &id || !old.manual || old.endpoint.ip != e.ip);
        let locked = self.session.as_ref().is_some_and(|s| s.id == id);
        match self.entries.get_mut(&id) {
            Some(entry) => {
                entry.manual = true;
                entry.missed = 0;
                if let Some(next) = choose(&entry.endpoint, &[e], locked) {
                    let moved = !same_address(&next, &entry.endpoint);
                    entry.endpoint = next;
                    if moved {
                        self.end_session(&id);
                    }
                }
            }
            None => {
                self.entries.insert(
                    id.clone(),
                    Entry {
                        endpoint: e,
                        manual: true,
                        missed: 0,
                    },
                );
            }
        }
        Ok(entry_bridge(&self.entries[&id]))
    }
    fn endpoint(&self, id: &str) -> Result<Endpoint, String> {
        self.entries
            .get(id)
            .map(|entry| entry.endpoint.clone())
            .ok_or_else(|| "Bridge selection is stale".into())
    }
    /// The session of this bridge, if it is still usable.
    fn session_for(&self, id: &str) -> Result<(Endpoint, String), String> {
        let session = self
            .session
            .as_ref()
            .filter(|s| s.id == id && Instant::now() < s.expires)
            .ok_or(SESSION_EXPIRED)?;
        Ok((session.endpoint.clone(), session.token.clone()))
    }
    /// A token obtained at this address may be kept: no forget since, the bridge has not moved.
    fn may_keep(&self, id: &str, e: &Endpoint, epoch: u64) -> bool {
        self.epoch == epoch
            && self
                .entries
                .get(id)
                .is_some_and(|entry| same_address(&entry.endpoint, e))
    }
    /// Keep what a refresh read. A new token taken meanwhile (password changed) keeps the session;
    /// what to add is only kept for the token that read it.
    fn refreshed(
        &mut self,
        id: &str,
        token: &str,
        addable: HashMap<String, Addable>,
        categories: HashMap<String, String>,
    ) -> Result<(), String> {
        let session = self
            .session
            .as_mut()
            .filter(|s| s.id == id)
            .ok_or(SESSION_EXPIRED)?;
        if session.token == token {
            session.addable = addable;
            session.categories = categories;
        }
        Ok(())
    }
    /// Drops the session only if it still holds this token; true when it did.
    fn drop_token(&mut self, token: &str) -> bool {
        if self.session.as_ref().is_some_and(|s| s.token == token) {
            self.session = None;
            return true;
        }
        false
    }
}
fn unauthorized(error: &str) -> bool {
    matches!(
        error,
        "Bridge returned HTTP 401" | "Bridge returned HTTP 403"
    )
}
/// Two endpoints at the same address, reached the same way.
fn same_address(a: &Endpoint, b: &Endpoint) -> bool {
    a.host == b.host
        && a.ip == b.ip
        && a.port == b.port
        && a.secure == b.secure
        && a.hotspot == b.hotspot
}
/// Where a known bridge goes after a search round. A strictly safer address (verified HTTPS, then a
/// confirmed direct hotspot) is always taken; otherwise its own address heard again. An address no
/// longer heard is left only when nothing ties the bridge to it: not added by hand, no session.
/// Another machine announcing the same identity from elsewhere never takes it over.
fn choose(old: &Endpoint, candidates: &[Endpoint], keep: bool) -> Option<Endpoint> {
    let best = candidates.iter().min_by_key(|e| preference(e))?;
    if preference(best).0 < preference(old).0 {
        return Some(best.clone());
    }
    if let Some(same) = candidates.iter().find(|c| same_address(c, old)) {
        return Some(same.clone());
    }
    (!keep).then(|| best.clone())
}
/// Sign out of a session that is no longer wanted, where it was obtained; best effort.
fn end(session: Session) {
    let e = &session.endpoint;
    if let Ok(c) = client(e) {
        revoke(&c, e, &session.token);
    }
}
/// Sign out of the sessions merge or insert_manual dropped.
fn end_dropped(launch: &Mutex<Launch>) {
    let ending = std::mem::take(&mut lock(launch).ending);
    for session in ending {
        end(session);
    }
}
/// Merge a search round, then sign out of a session whose bridge moved or left.
pub fn merge(launch: &Mutex<Launch>, scan: Scan) -> Vec<Bridge> {
    let bridges = lock(launch).merge(scan);
    end_dropped(launch);
    bridges
}
/// Probe a typed address; a bridge that answers is kept in the list as added manually.
pub fn add_manual(launch: &Mutex<Launch>, address: &str) -> Result<Bridge, String> {
    let ip = manual_address(address)?;
    if !local(ip) {
        return Err("Bridge is not on an approved local route".into());
    }
    let mut endpoint = Endpoint {
        host: ip.to_string(),
        ip,
        port: 80,
        secure: false,
        hotspot: false,
        legacy: false,
        identity: Identity {
            id: String::new(),
            name: String::new(),
            version: String::new(),
            path: String::new(),
        },
        public: Public::default(),
    };
    let c = client(&endpoint)?;
    probe_manual(&c, &mut endpoint)?;
    let bridge = lock(launch).insert_manual(endpoint);
    end_dropped(launch);
    bridge
}
/// Sign in once with the password; Setup keeps only the token, in memory, for this window.
pub fn unlock(launch: &Mutex<Launch>, id: &str, password: &str) -> Result<BridgeDetails, String> {
    let (e, epoch) = {
        let guard = lock(launch);
        (guard.endpoint(id)?, guard.epoch)
    };
    let (c, token) = auth(&e, password)?;
    let (details, addable, categories) = match read_all(&c, &e, &token, &HashMap::new()) {
        Ok(value) => value,
        Err(error) => {
            revoke(&c, &e, &token);
            return Err(error);
        }
    };
    let previous = {
        let mut guard = lock(launch);
        // Forgotten meanwhile (page left, other bridge), or the bridge moved: do not keep it.
        if !guard.may_keep(id, &e, epoch) {
            drop(guard);
            revoke(&c, &e, &token);
            return Err(SESSION_EXPIRED.into());
        }
        guard.session.replace(Session {
            id: id.into(),
            endpoint: e.clone(),
            token,
            expires: Instant::now() + SESSION_TTL,
            categories,
            addable,
        })
    };
    if let Some(previous) = previous {
        end(previous);
    }
    Ok(details)
}
/// Read the details again with the kept token, without the password.
pub fn refresh(launch: &Mutex<Launch>, id: &str) -> Result<BridgeDetails, String> {
    let (e, token, categories) = {
        let guard = lock(launch);
        let (e, token) = guard.session_for(id)?;
        let categories = guard
            .session
            .as_ref()
            .map(|s| s.categories.clone())
            .unwrap_or_default();
        (e, token, categories)
    };
    let c = client(&e)?;
    verify(&c, &e)?;
    match read_all(&c, &e, &token, &categories) {
        Ok((details, addable, categories)) => {
            lock(launch).refreshed(id, &token, addable, categories)?;
            Ok(details)
        }
        // A refusal of an older token (the password was just changed and the session
        // renewed meanwhile) is not an expiry: the page keeps the new session.
        Err(error) if unauthorized(&error) && lock(launch).drop_token(&token) => {
            Err(SESSION_EXPIRED.into())
        }
        Err(error) => Err(error),
    }
}
/// Forget the password: the token is revoked and dropped.
pub fn forget(launch: &Mutex<Launch>) {
    let previous = {
        let mut guard = lock(launch);
        guard.epoch += 1;
        guard.session.take()
    };
    if let Some(previous) = previous {
        end(previous);
    }
}
/// What adding a mount led to, said by the page.
#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AddOutcome {
    /// A USB serial mount: the bridge starts its relay on its own once it is declared.
    Added,
    /// A Wi-Fi mount: added, and the bridge accepted to connect it on a free adapter.
    Connecting,
    /// A Wi-Fi mount added, but no Wi-Fi adapter of the bridge is free for it.
    NoFreeAdapter,
    /// A Wi-Fi mount added, but the bridge refused or missed the connection request.
    NotConnected,
}
/// Add a mount the bridge found: a Wi-Fi mount profile or a USB serial mount.
pub fn add_device(launch: &Mutex<Launch>, id: &str, device: &str) -> Result<AddOutcome, String> {
    let (e, token, item) = {
        let guard = lock(launch);
        let (e, token) = guard.session_for(id)?;
        let item = guard
            .session
            .as_ref()
            .and_then(|s| s.addable.get(device).cloned())
            .ok_or("This device is no longer offered by the bridge")?;
        (e, token, item)
    };
    let c = client(&e)?;
    verify(&c, &e)?;
    match add_authenticated(&c, &e, &token, &item) {
        Err(error) if unauthorized(&error) => {
            lock(launch).drop_token(&token);
            Err(SESSION_EXPIRED.into())
        }
        result => result,
    }
}
fn add_authenticated(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    token: &str,
    item: &Addable,
) -> Result<AddOutcome, String> {
    match item {
        Addable::Wifi(observation) => {
            let candidate = request(
                c,
                e,
                reqwest::Method::GET,
                &format!("/api/v1/discovery/{observation}"),
                Some(token),
                None,
            )?;
            let body = wifi_profile(&candidate)?;
            let created = request(
                c,
                e,
                reqwest::Method::POST,
                "/api/v1/telescopes",
                Some(token),
                Some(body),
            )?;
            // The bridge saves a new profile inactive and without an adapter
            // (api.py update: enabled defaults to false): ask for the connection like
            // the web interface does, on a free adapter.
            Ok(connect_added(c, e, token, &created))
        }
        Addable::Serial(proposal) => request(
            c,
            e,
            reqwest::Method::POST,
            "/api/v1/serial-mounts",
            Some(token),
            Some(proposal.clone()),
        )
        .map(|_| AddOutcome::Added),
    }
}
/// POST /telescopes/{id}/connect for a profile just created, on a free adapter read from /status.
fn connect_added(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    token: &str,
    created: &Value,
) -> AddOutcome {
    let Some(profile) = created
        .pointer("/telescope/id")
        .and_then(Value::as_str)
        .filter(|id| valid_profile_id(id))
    else {
        return AddOutcome::NotConnected;
    };
    let previous = created
        .pointer("/telescope/mac")
        .and_then(Value::as_str)
        .unwrap_or("");
    // A change made meanwhile (HTTP 412) is read again once; never more.
    for _ in 0..2 {
        let Ok(status) = request_limited(
            c,
            e,
            reqwest::Method::GET,
            "/api/v1/status",
            Some(token),
            None,
            BIG,
        ) else {
            return AddOutcome::NotConnected;
        };
        let Some(mac) = free_adapter(&status) else {
            return AddOutcome::NoFreeAdapter;
        };
        let revision = status
            .get("config_revision")
            .and_then(Value::as_str)
            .filter(|r| {
                !r.is_empty()
                    && r.len() <= 128
                    && r.bytes().all(|b| b.is_ascii_graphic() && b != b'"')
            });
        match request_checked(
            c,
            e,
            reqwest::Method::POST,
            &format!("/api/v1/telescopes/{profile}/connect"),
            Some(token),
            Some(json!({"mac": mac, "expected_mac": previous})),
            LIMIT,
            revision,
        ) {
            Ok(_) => return AddOutcome::Connecting,
            Err(error) if error == "Bridge returned HTTP 412" => continue,
            Err(_) => return AddOutcome::NotConnected,
        }
    }
    AddOutcome::NotConnected
}
fn valid_profile_id(id: &str) -> bool {
    // config.py validate_config: [a-z0-9][a-z0-9-]{0,31}
    !id.is_empty()
        && id.len() <= 32
        && !id.starts_with('-')
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
fn valid_mac(mac: &str) -> bool {
    mac.len() == 17
        && mac.split(':').all(|part| {
            part.len() == 2
                && part
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
}
/// A Wi-Fi adapter of the bridge that may take a mount without taking anything from
/// another role, with the rules of the bridge (all read in /status):
/// - not the private-network adapter (controller.telescope_action, app.js connectionGroups);
/// - its role allows a telescope: auto or telescope, where the home and hotspot adapters
///   count as reserved (radio_roles.interface_roles / role_allows, app.js radioAllows);
/// - not the adapter carrying the active hotspot (app.js connectionGroups `reserved`,
///   controller.guard_management);
/// - not an adapter already used by an enabled profile or a connected mount, and not a
///   wired one (controller.free_radios).
///
/// USB adapters come first: the built-in one is the one the bridge falls back to for its
/// hotspot when no adapter is reserved for it.
fn free_adapter(status: &Value) -> Option<String> {
    let mac_at = |value: &Value, pointer: &str| {
        value
            .pointer(pointer)
            .and_then(Value::as_str)
            .map(str::to_ascii_lowercase)
            .filter(|m| valid_mac(m))
    };
    let home = mac_at(status, "/config/home/mac");
    let hotspot_config = mac_at(status, "/config/hotspot/mac");
    let mut roles: HashMap<String, String> = status
        .pointer("/config/interface_roles")
        .and_then(Value::as_object)
        .map(|roles| {
            roles
                .iter()
                .filter_map(|(mac, role)| Some((mac.to_ascii_lowercase(), role.as_str()?.into())))
                .collect()
        })
        .unwrap_or_default();
    for (mac, role) in [(&home, "home"), (&hotspot_config, "hotspot")] {
        if let Some(mac) = mac {
            roles.entry(mac.clone()).or_insert_with(|| role.into());
        }
    }
    let mut used: Vec<String> = home.iter().cloned().collect();
    if let Some(mac) = mac_at(status, "/hotspot/mac") {
        // free_radios leaves out the hotspot radio; the web interface does while it is active.
        used.push(mac);
    }
    for row in status
        .pointer("/config/telescopes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|p| p.get("enabled").and_then(Value::as_bool) == Some(true))
    {
        used.extend(mac_at(row, "/mac"));
    }
    for row in status
        .get("telescopes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|p| p.get("connected").and_then(Value::as_bool) == Some(true))
    {
        used.extend(mac_at(row, "/mac"));
    }
    let mut candidates: Vec<(bool, String)> = status
        .get("devices")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .take(32)
        .filter(|d| d.get("kind").and_then(Value::as_str).unwrap_or("wifi") != "ethernet")
        .filter_map(|d| {
            Some((
                d.get("usb").and_then(Value::as_bool) != Some(true),
                mac_at(d, "/mac")?,
            ))
        })
        .filter(|(_, mac)| {
            !used.contains(mac)
                && matches!(
                    roles.get(mac).map(String::as_str).unwrap_or("auto"),
                    "auto" | "telescope"
                )
        })
        .collect();
    candidates.sort_by_key(|(builtin, _)| *builtin);
    candidates.into_iter().next().map(|(_, mac)| mac)
}
pub fn change(
    launch: &Mutex<Launch>,
    id: &str,
    current: &str,
    new: &str,
    target: &str,
) -> Result<ChangeResult, String> {
    // "all": the single bridge password (web, hotspot and SSH); older targets stay accepted.
    if !matches!(target, "all" | "web" | "hotspot") {
        return Err("Unsupported password target".into());
    }
    let (e, epoch) = {
        let guard = lock(launch);
        (guard.endpoint(id)?, guard.epoch)
    };
    let (c, t) = auth(&e, current)?;
    let result = change_authenticated(&c, &e, &t, current, new, target);
    revoke(&c, &e, &t);
    let mut result = result?;
    // The kept session belongs to the old password: end it, then sign in with the new one.
    let previous = {
        let mut guard = lock(launch);
        if guard.session.as_ref().is_some_and(|s| s.id == id) {
            guard.session.take()
        } else {
            None
        }
    };
    if let Some(previous) = previous {
        let categories = previous.categories.clone();
        if !result.sessions_invalidated {
            end(previous);
        }
        if !result.hotspot_reconnect {
            if let Ok(token) = login(&c, &e, new) {
                let mut guard = lock(launch);
                if guard.session.is_none() && guard.may_keep(id, &e, epoch) {
                    guard.session = Some(Session {
                        id: id.into(),
                        endpoint: e.clone(),
                        token,
                        expires: Instant::now() + SESSION_TTL,
                        categories,
                        addable: HashMap::new(),
                    });
                    result.session_kept = true;
                } else {
                    drop(guard);
                    revoke(&c, &e, &token);
                }
            }
        }
    }
    Ok(result)
}
pub fn open(launch: &Mutex<Launch>, id: &str) -> Result<(), String> {
    let e = lock(launch).endpoint(id)?;
    let c = client(&e)?;
    verify(&c, &e)?;
    let url = admin_url(&e);
    let parsed = web_url(&url).ok_or("Invalid bridge address")?;
    if parsed.host_str() != Some(e.host.as_str())
        && parsed.host_str() != Some(e.ip.to_string().as_str())
    {
        return Err("Invalid bridge address".into());
    }
    open_url(&url)
}
/// Opens an address the caller has already checked in the default browser.
pub fn open_url(url: &str) -> Result<(), String> {
    {
        #[cfg(target_os = "macos")]
        let status = std::process::Command::new("/usr/bin/open")
            .arg(url)
            .status();
        #[cfg(target_os = "windows")]
        let status = std::process::Command::new("rundll32.exe")
            .arg("url.dll,FileProtocolHandler")
            .arg(url)
            .status();
        #[cfg(target_os = "linux")]
        let status = std::process::Command::new("xdg-open").arg(url).status();
        if status.map_err(|_| "Cannot open browser")?.success() {
            Ok(())
        } else {
            Err("Cannot open browser".into())
        }
    }
}
fn text(value: Option<&Value>, max: usize) -> Option<String> {
    let value = value?.as_str()?.trim();
    (!value.is_empty() && value.chars().count() <= max && !value.chars().any(char::is_control))
        .then(|| value.to_owned())
}
fn interfaces(status: &Value) -> Option<Interfaces> {
    let devices = status.get("devices")?.as_array()?;
    let usb = devices
        .iter()
        .filter(|d| d.get("usb").and_then(Value::as_bool) == Some(true))
        .count()
        .min(32) as u8;
    let total = devices.len().min(32) as u8;
    Some(Interfaces {
        total,
        builtin: total - usb,
        usb,
    })
}
fn connection_mode(status: &Value) -> Option<String> {
    let mode = status.pointer("/config/connection_mode")?.as_str()?;
    matches!(mode, "private" | "hotspot_isolated" | "hotspot_shared").then(|| mode.into())
}
fn hotspot_subnet(status: &Value) -> Option<String> {
    status
        .pointer("/management/paths")?
        .as_array()?
        .iter()
        .filter(|p| p.get("role").and_then(Value::as_str) == Some("ap"))
        .find_map(|p| {
            let subnet = p.get("subnet")?.as_str()?;
            let (ip, bits) = subnet.split_once('/')?;
            (ip.parse::<Ipv4Addr>().is_ok() && bits.parse::<u8>().is_ok_and(|b| b <= 32))
                .then(|| subnet.into())
        })
}
fn plugin_categories(value: &Value) -> HashMap<String, String> {
    value
        .get("plugins")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .take(64)
                .filter_map(|p| Some((text(p.get("id"), 32)?, text(p.get("category"), 32)?)))
                .collect()
        })
        .unwrap_or_default()
}
/// Wi-Fi mounts and telescopes heard by the bridge, and USB serial adapters not added yet.
fn found_devices(
    status: Option<&Value>,
    devices: Option<&Value>,
    categories: &HashMap<String, String>,
) -> (Vec<FoundDevice>, HashMap<String, Addable>) {
    let mut found = Vec::new();
    let mut addable = HashMap::new();
    let nearby = status
        .and_then(|s| s.pointer("/nearby_telescopes/candidates"))
        .and_then(Value::as_array);
    for row in nearby.into_iter().flatten().take(24) {
        let (Some(observation), Some(ssid), Some(kind), Some(signal)) = (
            row.get("observation_id").and_then(Value::as_str),
            text(row.get("ssid"), 32),
            row.get("kind").and_then(Value::as_str),
            row.get("signal")
                .and_then(Value::as_u64)
                .filter(|s| *s <= 100),
        ) else {
            continue;
        };
        if observation.len() != 32 || !observation.bytes().all(|b| b.is_ascii_hexdigit()) {
            continue;
        }
        let category = categories.get(kind).map(String::as_str);
        let mount = category == Some("mount");
        let id = format!("wifi-{observation}");
        if mount {
            addable.insert(id.clone(), Addable::Wifi(observation.into()));
        }
        found.push(FoundDevice {
            id,
            name: ssid,
            kind: match category {
                Some("mount") => "mount",
                Some("telescope") => "telescope",
                _ => "other",
            }
            .into(),
            link: "wifi".into(),
            signal: Some(signal as u8),
            can_add: mount,
        });
    }
    let detected = devices
        .and_then(|d| d.get("detected"))
        .and_then(Value::as_array);
    for row in detected.into_iter().flatten().take(24) {
        let Some(proposal) = row.get("proposal").filter(|p| p.is_object()) else {
            continue;
        };
        let (Some(name), Some(device)) = (
            text(proposal.get("name"), 64),
            text(proposal.get("device"), 256),
        ) else {
            continue;
        };
        let id = format!("serial-{device}");
        let can_add = row.get("can_add").and_then(Value::as_bool) == Some(true);
        if can_add {
            addable.insert(id.clone(), Addable::Serial(proposal.clone()));
        }
        found.push(FoundDevice {
            id,
            name,
            kind: "mount".into(),
            link: "serial_usb".into(),
            signal: None,
            can_add,
        });
    }
    (found, addable)
}
fn cameras(value: &Value) -> Vec<Camera> {
    let rows = value
        .as_array()
        .or_else(|| value.get("cameras").and_then(Value::as_array));
    rows.into_iter()
        .flatten()
        .take(24)
        .filter_map(|row| {
            let state = row
                .pointer("/discovery/state")
                .and_then(Value::as_str)
                .unwrap_or("ready");
            if !matches!(
                state,
                "detected" | "preparing" | "ready" | "driver_missing" | "unsupported"
            ) {
                return None;
            }
            Some(Camera {
                name: text(row.get("name"), 64)?,
                state: state.into(),
            })
        })
        .collect()
}
/// On a bridge where nothing was added, GET /devices lists the empty legacy
/// single-telescope field as a Wi-Fi telescope "primary" (devices.declared_devices reads
/// config.profiles on the public config, whose redacted "telescope" is never empty). The
/// real saved profiles are config.telescopes in /status: a "primary" row missing there is
/// not a device. Without /status nothing is left out.
fn phantom_primary(row: &Value, status: Option<&Value>) -> bool {
    let Some(saved) = status
        .and_then(|s| s.pointer("/config/telescopes"))
        .and_then(Value::as_array)
    else {
        return false;
    };
    row.get("id").and_then(Value::as_str) == Some("primary")
        && row.get("link_type").and_then(Value::as_str) == Some("wifi")
        && row.get("access").and_then(Value::as_str) == Some("direct")
        && !saved
            .iter()
            .any(|p| p.get("id").and_then(Value::as_str) == Some("primary"))
}
/// Mounts and telescopes already added; cameras have their own list.
fn added_devices(value: &Value, status: Option<&Value>) -> Vec<AddedDevice> {
    value
        .get("devices")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .take(48)
        .filter(|row| !phantom_primary(row, status))
        .filter_map(|row| {
            let kind = row.get("kind")?.as_str()?;
            let link_type = row.get("link_type")?.as_str()?;
            let state = row.get("state")?.as_str()?;
            if !matches!(kind, "mount" | "smart_telescope" | "other")
                || !matches!(link_type, "wifi" | "serial_usb" | "usb")
                || !matches!(state, "online" | "offline" | "unknown")
            {
                return None;
            }
            Some(AddedDevice {
                name: text(row.get("name"), 64)?,
                kind: kind.into(),
                link_type: link_type.into(),
                state: state.into(),
                port: (link_type == "serial_usb")
                    .then(|| row.pointer("/endpoint/port")?.as_u64())
                    .flatten()
                    .and_then(|p| u16::try_from(p).ok()),
            })
        })
        .collect()
}
/// The profile of a Wi-Fi mount, from the bridge observation; never a guessed password.
fn wifi_profile(candidate: &Value) -> Result<Value, String> {
    let invalid = || "Invalid bridge response".to_string();
    let ssid = text(candidate.get("ssid"), 32).ok_or_else(invalid)?;
    let suggested = candidate.get("suggested").ok_or_else(invalid)?;
    let kind = suggested
        .get("kind")
        .and_then(Value::as_str)
        .filter(|k| {
            !k.is_empty()
                && k.len() <= 32
                && k.bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
        })
        .ok_or_else(invalid)?;
    let name = text(suggested.get("name"), 64).unwrap_or_else(|| ssid.clone());
    let target = suggested
        .get("target_ip")
        .and_then(Value::as_str)
        .filter(|ip| ip.parse::<Ipv4Addr>().is_ok())
        .ok_or(USE_ADMIN_INTERFACE)?;
    let security = candidate
        .get("security")
        .and_then(Value::as_str)
        .unwrap_or("");
    let mut body = json!({"name": name, "kind": kind, "ssid": ssid, "target_ip": target});
    if let Some(bssid) = candidate.get("bssid").and_then(Value::as_str).filter(|b| {
        b.len() == 17
            && b.split(':')
                .all(|part| part.len() == 2 && part.bytes().all(|c| c.is_ascii_hexdigit()))
    }) {
        body["bssid"] = json!(bssid);
    }
    if security.is_empty() || security == "--" {
        body["open"] = json!(true);
    } else {
        let password = suggested
            .get("password")
            .and_then(Value::as_str)
            .filter(|p| (8..=63).contains(&p.len()))
            .ok_or(USE_ADMIN_INTERFACE)?;
        body["password"] = json!(password);
    }
    Ok(body)
}
fn read_all(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    t: &str,
    known: &HashMap<String, String>,
) -> Result<
    (
        BridgeDetails,
        HashMap<String, Addable>,
        HashMap<String, String>,
    ),
    String,
> {
    let get = |path: &str, limit: usize| {
        request_limited(c, e, reqwest::Method::GET, path, Some(t), None, limit)
    };
    let home = get("/api/v1/home", LIMIT)?;
    let hot = get("/api/v1/hotspot", LIMIT)?;
    // Older bridges miss some of these: the page then shows what it could read.
    let status = get("/api/v1/status", BIG).ok();
    let devices = get("/api/v1/devices", BIG).ok();
    let camera_rows = get("/api/v1/cameras", BIG).ok();
    let categories = if known.is_empty() {
        get("/api/v1/plugins", BIG)
            .ok()
            .map(|p| plugin_categories(&p))
            .unwrap_or_default()
    } else {
        known.clone()
    };
    let (found, addable) = found_devices(status.as_ref(), devices.as_ref(), &categories);
    let details = BridgeDetails {
        ssh_account: status.as_ref().and_then(ssh_account),
        home_ssid: home
            .pointer("/home/ssid")
            .and_then(Value::as_str)
            .unwrap_or("")
            .into(),
        home_connected: home
            .pointer("/home/connection/connected")
            .and_then(Value::as_bool)
            .ok_or("Invalid home status")?,
        hotspot_ssid: hot
            .pointer("/hotspot/ssid")
            .and_then(Value::as_str)
            .unwrap_or("")
            .into(),
        hotspot_active: hot
            .pointer("/hotspot/connection/active")
            .and_then(Value::as_bool)
            .ok_or("Invalid hotspot status")?,
        interfaces: status.as_ref().and_then(interfaces),
        connection_mode: status.as_ref().and_then(connection_mode),
        hotspot_subnet: status.as_ref().and_then(hotspot_subnet),
        found,
        cameras: camera_rows.as_ref().map(cameras).unwrap_or_default(),
        added: devices
            .as_ref()
            .map(|d| added_devices(d, status.as_ref()))
            .unwrap_or_default(),
    };
    Ok((details, addable, categories))
}
fn change_authenticated(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    t: &str,
    current: &str,
    new: &str,
    target: &str,
) -> Result<ChangeResult, String> {
    let result = (|| {
        let v = request(
            c,
            e,
            reqwest::Method::POST,
            "/api/v1/credentials",
            Some(t),
            Some(json!({"target":target,"current_password":current,"password":new})),
        )?;
        Ok(ChangeResult {
            sessions_invalidated: v
                .get("sessions_invalidated")
                .and_then(Value::as_bool)
                .ok_or("Invalid credentials response")?,
            hotspot_reconnect: v
                .get("hotspot_reconnect")
                .and_then(Value::as_bool)
                .ok_or("Invalid credentials response")?,
            session_kept: false,
        })
    })();
    result
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ssh_account_is_a_plain_linux_name_or_nothing() {
        assert_eq!(
            ssh_account(&json!({"ssh": {"account": "root", "password_login": true}})).as_deref(),
            Some("root")
        );
        assert_eq!(
            ssh_account(&json!({"ssh": {"account": "bridge"}})).as_deref(),
            Some("bridge")
        );
        for bad in [
            json!({}),
            json!({"ssh": {"account": null}}),
            json!({"ssh": {"account": "Root"}}),
            json!({"ssh": {"account": "a b"}}),
            json!({"ssh": {"account": "x;rm"}}),
            json!({"ssh": {"account": ""}}),
        ] {
            assert_eq!(ssh_account(&bad), None);
        }
    }
    #[test]
    fn rejects_bad_id_and_host() {
        assert!(valid_id("0123456789abcdef"));
        assert!(!valid_id("0123456789abcdeF"));
        assert!(safe_host("bridge.local."));
        assert!(!safe_host("evil.local@remote.example"));
    }
    #[test]
    fn manual_address_and_identity_are_constrained() {
        for value in [
            "8.8.8.8",
            "127.0.0.1",
            "::1",
            "http://192.168.1.1",
            "192.168.1.1:80",
            "192.168.001.1",
            " 192.168.1.1",
        ] {
            assert!(manual_address(value).is_err(), "{value}");
        }
        assert_eq!(
            manual_address("10.23.45.67").unwrap(),
            Ipv4Addr::new(10, 23, 45, 67)
        );
        assert!(manual_address("169.254.2.3").is_ok());
        let mut value = json!({"device_id":"0123456789abcdef","name":"Bridge","version":"1.0","request_path":"home","product":"astro-bridge","api_version":"1","contract_version":1});
        assert!(manual_identity(&value).is_ok());
        value["request_path"] = json!("hotspot");
        assert!(manual_identity(&value).is_err());
        value["request_path"] = json!("other");
        assert!(manual_identity(&value).is_err());
        value["request_path"] = json!("home");
        value["product"] = json!("other");
        assert!(manual_identity(&value).is_err());
        let launch = Mutex::new(Launch::default());
        assert!(add_manual(&launch, "8.8.8.8").is_err());
        assert!(lock(&launch).entries.is_empty());
    }
    #[test]
    fn validates_identity() {
        let mut v = json!({"device_id":"0123456789abcdef","name":"Bridge","version":"1.0","request_path":"home","product":"astro-bridge","api_version":"1","contract_version":1});
        assert!(identity(&v).is_ok());
        v["product"] = json!("other");
        assert!(identity(&v).is_err());
    }
    #[test]
    fn manual_probe_checks_identity_and_path_over_http() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        for (path, success) in [("home", true), ("other", false), ("hotspot", false)] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let body = json!({"device_id":"0123456789abcdef","name":"Bridge","version":"1.0","request_path":path,"product":"astro-bridge","api_version":"1","contract_version":1}).to_string();
            let server = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut buf = [0u8; 2048];
                let n = stream.read(&mut buf).unwrap();
                assert!(String::from_utf8_lossy(&buf[..n]).starts_with("GET /api/v1/info "));
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                )
                .unwrap();
            });
            let mut e = Endpoint {
                host: "127.0.0.1".into(),
                ip: Ipv4Addr::LOCALHOST,
                port,
                secure: false,
                hotspot: false,
                legacy: false,
                identity: Identity {
                    id: String::new(),
                    name: String::new(),
                    version: String::new(),
                    path: String::new(),
                },
                public: Public::default(),
            };
            let c = reqwest::blocking::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap();
            assert_eq!(probe_manual(&c, &mut e).is_ok(), success);
            if success {
                assert_eq!(e.identity.path, "home");
                assert!(auth(&e, "password").is_err());
            }
            server.join().unwrap();
        }
    }
    #[test]
    fn http_rejects_redirect_and_oversized_response() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        for payload in [
            "HTTP/1.1 302 Found\r\nLocation: http://example.com/\r\nContent-Length: 0\r\n\r\n"
                .to_string(),
            format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n", LIMIT + 1),
        ] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let server = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut buf = [0u8; 2048];
                let _ = stream.read(&mut buf);
                stream.write_all(payload.as_bytes()).unwrap();
            });
            let e = Endpoint {
                host: "127.0.0.1".into(),
                ip: Ipv4Addr::LOCALHOST,
                port,
                secure: false,
                hotspot: false,
                legacy: false,
                identity: Identity {
                    id: "0123456789abcdef".into(),
                    name: "B".into(),
                    version: "1".into(),
                    path: "other".into(),
                },
                public: Public::default(),
            };
            let c = reqwest::blocking::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(2))
                .build()
                .unwrap();
            assert!(request(&c, &e, reqwest::Method::GET, "/api/v1/info", None, None).is_err());
            server.join().unwrap();
        }
    }
    #[test]
    fn route_and_preference_are_conservative() {
        assert!(mac_direct_route(
            "interface: en0\nflags: <UP,HOST,LLINFO>\ngateway: aa:b:0:12:ff:03\n",
            "en0"
        ));
        assert!(mac_direct_route(
            "interface: en0\nflags: <UP>\ngateway: link#4\n",
            "en0"
        ));
        assert!(!mac_direct_route(
            "interface: en0\nflags: <UP,GATEWAY>\ngateway: 192.168.77.2\n",
            "en0"
        ));
        assert!(!mac_direct_route(
            "interface: en1\nflags: <UP,LLINFO>\ngateway: aa:bb:cc:dd:ee:ff\n",
            "en0"
        ));
        let mut e = Endpoint {
            host: "192.168.77.1".into(),
            ip: HOTSPOT,
            port: 80,
            secure: false,
            hotspot: false,
            legacy: false,
            identity: Identity {
                id: "0123456789abcdef".into(),
                name: "Bridge".into(),
                version: "1".into(),
                path: "hotspot".into(),
            },
            public: Public::default(),
        };
        let plain = preference(&e).0;
        e.hotspot = true;
        let hotspot = preference(&e).0;
        e.secure = true;
        assert!(preference(&e).0 < hotspot && hotspot < plain);
    }
    #[test]
    fn fixture_checks_login_read_rotation_and_revoke() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let identity = json!({"device_id":"0123456789abcdef","name":"Bridge","version":"1","request_path":"other","product":"astro-bridge","api_version":"1","contract_version":1});
        let steps = vec![
            ("GET", "/api/v1/info", None, identity.clone()),
            (
                "POST",
                "/api/v1/auth/token",
                Some(json!({"password":"test-current"})),
                json!({"access_token":"test-token","token_type":"Bearer"}),
            ),
            (
                "GET",
                "/api/v1/home",
                None,
                json!({"home":{"ssid":"Home","connection":{"connected":true}}}),
            ),
            (
                "GET",
                "/api/v1/hotspot",
                None,
                json!({"hotspot":{"ssid":"Bridge AP","connection":{"active":true}}}),
            ),
            (
                "GET",
                "/api/v1/status",
                None,
                json!({"ssh":{"account":"root","password_login":true},
                    "devices":[{"name":"wlan0","usb":false},{"name":"wlan1","usb":true}],
                    "config":{"connection_mode":"hotspot_shared"},
                    "management":{"paths":[{"role":"home","subnet":"192.168.50.0/24"},{"role":"ap","subnet":"192.168.77.0/24"}]},
                    "nearby_telescopes":{"candidates":[
                        {"observation_id":"0123456789abcdef0123456789abcdef","ssid":"SynScan_1234","kind":"synscan","signal":82},
                        {"observation_id":"fedcba9876543210fedcba9876543210","ssid":"S50_abcd","kind":"seestar","signal":60}]}}),
            ),
            (
                "GET",
                "/api/v1/devices",
                None,
                json!({"devices":[
                    {"id":"m1","name":"NexStar","kind":"mount","link_type":"serial_usb","state":"online","endpoint":{"port":2000}},
                    {"id":"c1","name":"Guide camera","kind":"camera","link_type":"usb","state":"offline"}],
                    "detected":[{"can_add":true,"proposal":{"name":"NexStar","profile":"nexstar","device":"/dev/serial/by-id/usb-1","tcp_port":2001}}]}),
            ),
            (
                "GET",
                "/api/v1/cameras",
                None,
                json!([{"name":"ZWO ASI","discovery":{"state":"ready"}},{"name":"Other","discovery":{"state":"driver_missing"}}]),
            ),
            (
                "GET",
                "/api/v1/plugins",
                None,
                json!({"plugins":[{"id":"synscan","category":"mount"},{"id":"seestar","category":"telescope"}]}),
            ),
            (
                "DELETE",
                "/api/v1/auth/token",
                Some(json!({})),
                json!({"ok":true}),
            ),
            ("GET", "/api/v1/info", None, identity),
            (
                "POST",
                "/api/v1/auth/token",
                Some(json!({"password":"test-current"})),
                json!({"access_token":"test-token","token_type":"Bearer"}),
            ),
            (
                "POST",
                "/api/v1/credentials",
                Some(
                    json!({"target":"all","current_password":"test-current","password":"test-new"}),
                ),
                json!({"sessions_invalidated":false,"hotspot_reconnect":true}),
            ),
            (
                "DELETE",
                "/api/v1/auth/token",
                Some(json!({})),
                json!({"ok":true}),
            ),
        ];
        let server = std::thread::spawn(move || {
            for (method, path, expected, reply) in steps {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut b = [0u8; 1024];
                let (header_end, length) = loop {
                    let n = socket.read(&mut b).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&b[..n]);
                    if let Some(i) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&bytes[..i]);
                        let len = headers
                            .lines()
                            .find_map(|l| {
                                l.to_ascii_lowercase()
                                    .strip_prefix("content-length:")
                                    .map(|s| s.trim().parse::<usize>().unwrap())
                            })
                            .unwrap_or(0);
                        break (i + 4, len);
                    }
                };
                while bytes.len() < header_end + length {
                    let n = socket.read(&mut b).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&b[..n]);
                }
                let headers = String::from_utf8_lossy(&bytes[..header_end]);
                assert!(headers.starts_with(&format!("{method} {path} HTTP/1.1\r\n")));
                if path != "/api/v1/info" && path != "/api/v1/auth/token" || method == "DELETE" {
                    assert!(headers
                        .to_ascii_lowercase()
                        .contains("authorization: bearer test-token"));
                }
                if let Some(body) = expected {
                    assert_eq!(
                        serde_json::from_slice::<Value>(&bytes[header_end..header_end + length])
                            .unwrap(),
                        body
                    );
                }
                let body = reply.to_string();
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                )
                .unwrap();
            }
        });
        let e = Endpoint {
            host: "127.0.0.1".into(),
            ip: Ipv4Addr::LOCALHOST,
            port,
            secure: false,
            hotspot: false,
            legacy: false,
            identity: Identity {
                id: "0123456789abcdef".into(),
                name: "Bridge".into(),
                version: "1".into(),
                path: "other".into(),
            },
            public: Public::default(),
        };
        let c = reqwest::blocking::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap();
        let t = login(&c, &e, "test-current").unwrap();
        let (state, addable, categories) = read_all(&c, &e, &t, &HashMap::new()).unwrap();
        assert_eq!(state.home_ssid, "Home");
        assert!(state.home_connected);
        assert_eq!(state.hotspot_ssid, "Bridge AP");
        assert!(state.hotspot_active);
        assert_eq!(state.ssh_account.as_deref(), Some("root"));
        assert_eq!(
            state.interfaces,
            Some(Interfaces {
                total: 2,
                builtin: 1,
                usb: 1
            })
        );
        assert_eq!(state.connection_mode.as_deref(), Some("hotspot_shared"));
        assert_eq!(state.hotspot_subnet.as_deref(), Some("192.168.77.0/24"));
        // Only mounts can be added: the Wi-Fi SynScan and the serial adapter, not the Seestar.
        let kinds: Vec<_> = state
            .found
            .iter()
            .map(|f| (f.name.as_str(), f.kind.as_str(), f.link.as_str(), f.can_add))
            .collect();
        assert_eq!(
            kinds,
            [
                ("SynScan_1234", "mount", "wifi", true),
                ("S50_abcd", "telescope", "wifi", false),
                ("NexStar", "mount", "serial_usb", true)
            ]
        );
        assert_eq!(addable.len(), 2);
        assert_eq!(
            addable["wifi-0123456789abcdef0123456789abcdef"],
            Addable::Wifi("0123456789abcdef0123456789abcdef".into())
        );
        assert!(
            matches!(&addable["serial-/dev/serial/by-id/usb-1"], Addable::Serial(p) if p["tcp_port"] == 2001)
        );
        assert_eq!(categories["synscan"], "mount");
        assert_eq!(
            state.cameras,
            [
                Camera {
                    name: "ZWO ASI".into(),
                    state: "ready".into()
                },
                Camera {
                    name: "Other".into(),
                    state: "driver_missing".into()
                }
            ]
        );
        // Cameras are listed apart, not twice.
        assert_eq!(state.added.len(), 1);
        assert_eq!(state.added[0].port, Some(2000));
        revoke(&c, &e, &t);
        let t = login(&c, &e, "test-current").unwrap();
        let change = change_authenticated(&c, &e, &t, "test-current", "test-new", "all").unwrap();
        assert!(change.hotspot_reconnect);
        assert!(!change.sessions_invalidated);
        revoke(&c, &e, &t);
        server.join().unwrap();
        assert!(auth(&e, "test-current").is_err());
    }
    #[test]
    fn legacy_101_is_exact_and_never_authorizes_passwords() {
        let mut v: Value =
            serde_json::from_str(include_str!("../tests/legacy-info-1.0.1.json")).unwrap();
        let found = legacy_identity(&v).unwrap();
        assert_eq!(found.version, "1.0.1");
        assert_eq!(found.path, "other");
        let e = Endpoint {
            host: "192.168.77.1".into(),
            ip: HOTSPOT,
            port: 80,
            secure: false,
            hotspot: true,
            legacy: true,
            identity: found,
            public: Public::default(),
        };
        assert_eq!(
            auth(&e, "test-password").err().unwrap(),
            "This legacy bridge supports browser access only"
        );
        v["contract_version"] = json!(1);
        assert!(legacy_identity(&v).is_none());
        v.as_object_mut().unwrap().remove("contract_version");
        v["version"] = json!("1.0.2");
        assert!(legacy_identity(&v).is_none());
        v["version"] = json!("1.0.1");
        v["capabilities"] = json!([]);
        assert!(legacy_identity(&v).is_none());
    }
    fn found(id: &str, ip: [u8; 4], secure: bool) -> Endpoint {
        let ip = Ipv4Addr::from(ip);
        Endpoint {
            host: if secure {
                "bridge.local".into()
            } else {
                ip.to_string()
            },
            ip,
            port: if secure { 443 } else { 80 },
            secure,
            hotspot: false,
            legacy: false,
            identity: Identity {
                id: id.into(),
                name: format!("bridge-{id}"),
                version: "1.2.0".into(),
                path: "home".into(),
            },
            public: public_info(&json!({"hotspot": true, "management_endpoints": [
                {"url": format!("http://{ip}/"), "path": "home", "tls": false},
                {"url": "http://192.168.77.1/", "path": "hotspot", "tls": false}]})),
        }
    }
    #[test]
    fn identifiers_stay_the_same_from_one_search_to_the_next() {
        let mut launch = Launch::default();
        let a = "0123456789abcdef";
        let b = "fedcba9876543210";
        let first = launch.merge(Scan(vec![
            found(a, [192, 168, 50, 20], false),
            found(b, [192, 168, 50, 21], false),
        ]));
        let second = launch.merge(Scan(vec![
            found(b, [192, 168, 50, 21], false),
            found(a, [192, 168, 50, 20], false),
        ]));
        let ids = |list: &[Bridge]| list.iter().map(|x| x.id.clone()).collect::<Vec<_>>();
        assert_eq!(ids(&first), [a, b]);
        assert_eq!(ids(&first), ids(&second));
        assert_eq!(second[0].admin_url, "http://192.168.50.20/");
        assert_eq!(second[0].hotspot_active, Some(true));
        assert_eq!(second[0].addresses.len(), 2);
        // A bridge missing one or two rounds stays (mDNS is not always heard); after three it goes.
        assert_eq!(
            launch
                .merge(Scan(vec![found(a, [192, 168, 50, 20], false)]))
                .len(),
            2
        );
        assert_eq!(
            launch
                .merge(Scan(vec![found(a, [192, 168, 50, 20], false)]))
                .len(),
            2
        );
        assert_eq!(
            ids(&launch.merge(Scan(vec![found(a, [192, 168, 50, 20], false)]))),
            [a]
        );
        // A bridge on the home network in plain HTTP takes the password, like its web page.
        assert_eq!(launch.bridges()[0].auth_mode, "http");
        // The same bridge on HTTPS replaces its HTTP address, under the same identifier.
        let upgraded = launch.merge(Scan(vec![found(a, [192, 168, 50, 20], true)]));
        assert_eq!(ids(&upgraded), [a]);
        assert_eq!(upgraded[0].auth_mode, "https");
    }
    #[test]
    fn a_bridge_added_by_address_stays_between_search_rounds() {
        let mut launch = Launch::default();
        let manual = "00000000000000aa";
        let bridge = launch
            .insert_manual(found(manual, [192, 168, 50, 31], false))
            .unwrap();
        assert!(bridge.manual);
        assert_eq!(bridge.id, manual);
        for _ in 0..5 {
            let list = launch.merge(Scan(vec![found(
                "0123456789abcdef",
                [192, 168, 50, 20],
                false,
            )]));
            assert_eq!(list.len(), 2);
            // Listed last, still marked as added by hand.
            assert_eq!(list[1].id, manual);
            assert!(list[1].manual);
        }
        // Found by the search too: one card, still marked as added by hand.
        let list = launch.merge(Scan(vec![found(manual, [192, 168, 50, 31], false)]));
        assert_eq!(list.iter().filter(|x| x.id == manual).count(), 1);
        assert!(list.iter().find(|x| x.id == manual).unwrap().manual);
        // Another bridge now answering at the same address replaces the old manual card.
        launch
            .insert_manual(found("00000000000000bb", [192, 168, 50, 31], false))
            .unwrap();
        assert!(launch.entries.contains_key("00000000000000bb"));
        assert!(!launch.entries.contains_key(manual));
    }
    #[test]
    fn session_is_needed_and_forgotten_with_the_bridge() {
        let launch = Mutex::new(Launch::default());
        assert_eq!(
            refresh(&launch, "0123456789abcdef").err().as_deref(),
            Some(SESSION_EXPIRED)
        );
        lock(&launch).merge(Scan(vec![found(
            "0123456789abcdef",
            [192, 168, 50, 20],
            false,
        )]));
        assert_eq!(
            refresh(&launch, "0123456789abcdef").err().as_deref(),
            Some(SESSION_EXPIRED)
        );
        assert_eq!(
            add_device(&launch, "0123456789abcdef", "wifi-x")
                .err()
                .as_deref(),
            Some(SESSION_EXPIRED)
        );
        lock(&launch).session = Some(Session {
            id: "0123456789abcdef".into(),
            endpoint: found("0123456789abcdef", [192, 168, 50, 20], true),
            token: "test-token".into(),
            expires: Instant::now() + SESSION_TTL,
            categories: HashMap::new(),
            addable: HashMap::new(),
        });
        assert!(lock(&launch).session_for("0123456789abcdef").is_ok());
        assert_eq!(
            lock(&launch)
                .session_for("fedcba9876543210")
                .err()
                .as_deref(),
            Some(SESSION_EXPIRED)
        );
        // An expired token is never sent.
        lock(&launch).session.as_mut().unwrap().expires = Instant::now();
        assert_eq!(
            lock(&launch)
                .session_for("0123456789abcdef")
                .err()
                .as_deref(),
            Some(SESSION_EXPIRED)
        );
        lock(&launch).session.as_mut().unwrap().expires = Instant::now() + SESSION_TTL;
        // The bridge gone from the list takes its session with it.
        for _ in 0..MISSES {
            lock(&launch).merge(Scan(vec![]));
        }
        assert!(lock(&launch).session.is_none());
        lock(&launch).session = Some(Session {
            id: "gone".into(),
            endpoint: found("0123456789abcdef", [192, 168, 50, 20], true),
            token: "test-token".into(),
            expires: Instant::now() + SESSION_TTL,
            categories: HashMap::new(),
            addable: HashMap::new(),
        });
        forget(&launch);
        assert!(lock(&launch).session.is_none());
    }
    #[test]
    fn web_addresses_are_given_by_the_bridge_and_local_only() {
        for good in [
            "http://192.168.77.1/",
            "http://192.168.50.20:8797/",
            "https://bridge.local/",
        ] {
            assert!(web_url(good).is_some(), "{good}");
        }
        for bad in [
            "http://8.8.8.8/",
            "http://127.0.0.1/",
            "http://example.com/",
            "ftp://192.168.1.2/",
            "http://user@192.168.1.2/",
            "http://192.168.1.2/admin",
            "http://192.168.1.2/?x=1",
            "http://192.168.1.2",
            "javascript:alert(1)",
        ] {
            assert!(web_url(bad).is_none(), "{bad}");
        }
        let public = public_info(&json!({"hotspot": false, "management_endpoints": [
            {"url": "http://8.8.8.8/", "path": "home", "tls": false},
            {"url": "https://bridge.local/", "path": "home", "tls": false},
            {"url": "http://192.168.50.20/", "path": "other", "tls": false},
            {"url": "http://192.168.77.1/", "path": "hotspot", "tls": false}]}));
        assert_eq!(public.hotspot, Some(false));
        assert_eq!(public.addresses.len(), 1);
        assert_eq!(public.addresses[0].host, "192.168.77.1");
        // No list (older bridges): the address Setup reached, without the default port.
        let mut e = found("0123456789abcdef", [192, 168, 50, 20], false);
        e.public = Public::default();
        assert_eq!(admin_url(&e), "http://192.168.50.20/");
        // Reached on the hotspot: its own address comes first.
        let mut e = found("0123456789abcdef", [192, 168, 77, 1], false);
        e.identity.path = "hotspot".into();
        assert_eq!(admin_url(&e), "http://192.168.77.1/");
    }
    #[test]
    fn wifi_mount_profile_never_guesses_a_password() {
        let candidate = json!({"ssid": "SynScan_1234", "bssid": "aa:bb:cc:dd:ee:ff", "security": "",
            "suggested": {"kind": "synscan", "name": "SynScan mount", "target_ip": "192.168.4.1"}});
        assert_eq!(
            wifi_profile(&candidate).unwrap(),
            json!({"name": "SynScan mount", "kind": "synscan", "ssid": "SynScan_1234",
                "target_ip": "192.168.4.1", "bssid": "aa:bb:cc:dd:ee:ff", "open": true})
        );
        let mut secured = candidate.clone();
        secured["security"] = json!("WPA2");
        assert_eq!(
            wifi_profile(&secured).err().as_deref(),
            Some(USE_ADMIN_INTERFACE)
        );
        secured["suggested"]["password"] = json!("12345678");
        assert_eq!(wifi_profile(&secured).unwrap()["password"], "12345678");
        let mut unknown = candidate;
        unknown["suggested"]["target_ip"] = json!("");
        assert_eq!(
            wifi_profile(&unknown).err().as_deref(),
            Some(USE_ADMIN_INTERFACE)
        );
    }
    fn session(id: &str, endpoint: Endpoint, token: &str) -> Session {
        Session {
            id: id.into(),
            endpoint,
            token: token.into(),
            expires: Instant::now() + SESSION_TTL,
            categories: HashMap::new(),
            addable: HashMap::new(),
        }
    }
    fn hotspot(id: &str) -> Endpoint {
        let mut e = found(id, [192, 168, 77, 1], false);
        e.hotspot = true;
        e.identity.path = "hotspot".into();
        e
    }
    const ID: &str = "0123456789abcdef";
    #[test]
    fn token_from_the_hotspot_never_follows_a_plain_http_announcement() {
        // 1a: signed in on the direct hotspot, the computer is back on the home Wi-Fi.
        let mut launch = Launch::default();
        launch.merge(Scan(vec![hotspot(ID)]));
        launch.session = Some(session(ID, hotspot(ID), "t"));
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], false)]));
        let (e, token) = launch.session_for(ID).unwrap();
        assert!(e.hotspot && !e.secure && e.ip == HOTSPOT && token == "t");
        assert!(launch.entries[ID].endpoint.hotspot);
        assert!(launch.ending.is_empty());
    }
    #[test]
    fn https_bridge_does_not_fall_back_to_http_while_signed_in() {
        // 1b: the HTTPS probe missed one round (deadline or TLS hiccup).
        let mut launch = Launch::default();
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], true)]));
        launch.session = Some(session(ID, found(ID, [192, 168, 50, 20], true), "t"));
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], false)]));
        assert!(launch.entries[ID].endpoint.secure);
        assert!(launch.session_for(ID).unwrap().0.secure);
        // Without a session it may follow the bridge, but a token is never sent there.
        launch.session = None;
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], false)]));
        assert!(!launch.entries[ID].endpoint.secure);
        assert_eq!(
            launch.session_for(ID).err().as_deref(),
            Some(SESSION_EXPIRED)
        );
    }
    #[test]
    fn another_machine_with_the_same_identity_never_takes_the_entry() {
        // 1c: same device_id, name, version and caps announced from a lower address.
        let mut launch = Launch::default();
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], false)]));
        launch.merge(Scan(vec![
            found(ID, [192, 168, 50, 5], false),
            found(ID, [192, 168, 50, 20], false),
        ]));
        assert_eq!(
            launch.entries[ID].endpoint.ip,
            Ipv4Addr::new(192, 168, 50, 20)
        );
        // Alone, it does not take a bridge added by hand, nor one with a session.
        let mut manual = Launch::default();
        manual
            .insert_manual(found(ID, [192, 168, 50, 31], false))
            .unwrap();
        manual.merge(Scan(vec![found(ID, [192, 168, 50, 5], false)]));
        assert_eq!(
            manual.entries[ID].endpoint.ip,
            Ipv4Addr::new(192, 168, 50, 31)
        );
        launch.session = Some(session(ID, found(ID, [192, 168, 50, 20], true), "t"));
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 5], false)]));
        assert_eq!(
            launch.entries[ID].endpoint.ip,
            Ipv4Addr::new(192, 168, 50, 20)
        );
        assert!(launch.session.is_some());
    }
    #[test]
    fn a_moved_or_gone_bridge_signs_its_session_out() {
        // The entry moves to a safer address: the session obtained elsewhere is dropped and revoked.
        let mut launch = Launch::default();
        launch.merge(Scan(vec![hotspot(ID)]));
        launch.session = Some(session(ID, hotspot(ID), "t"));
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], true)]));
        assert!(launch.session.is_none());
        assert_eq!(launch.ending.len(), 1);
        assert!(launch.ending[0].endpoint.hotspot);
        // 4: the bridge leaves the network: its session is revoked, not only dropped.
        let launch = Mutex::new(Launch::default());
        lock(&launch).merge(Scan(vec![found(ID, [192, 168, 50, 20], true)]));
        lock(&launch).session = Some(session(ID, found(ID, [192, 168, 50, 20], true), "t"));
        for _ in 0..MISSES {
            lock(&launch).merge(Scan(vec![]));
        }
        assert!(lock(&launch).session.is_none());
        assert_eq!(lock(&launch).ending.len(), 1);
        // The public merge signs it out (no route here, so nothing is sent) and empties the list.
        merge(&launch, Scan(vec![]));
        assert!(lock(&launch).ending.is_empty());
    }
    #[test]
    fn a_sign_in_started_before_forget_or_a_move_is_not_kept() {
        // 3: the page was left, or another bridge chosen, while the password was being checked.
        let launch = Mutex::new(Launch::default());
        lock(&launch).merge(Scan(vec![found(ID, [192, 168, 50, 20], true)]));
        let e = lock(&launch).endpoint(ID).unwrap();
        let epoch = lock(&launch).epoch;
        assert!(lock(&launch).may_keep(ID, &e, epoch));
        forget(&launch);
        assert!(!lock(&launch).may_keep(ID, &e, epoch));
        let epoch = lock(&launch).epoch;
        let mut moved = e.clone();
        moved.ip = Ipv4Addr::new(192, 168, 50, 21);
        assert!(!lock(&launch).may_keep(ID, &moved, epoch));
        assert!(!lock(&launch).may_keep("fedcba9876543210", &e, epoch));
    }
    #[test]
    fn a_refresh_overlapping_a_new_token_keeps_the_session() {
        // 2: the password changed (new token) while a refresh with the old one was reading.
        let mut launch = Launch::default();
        launch.merge(Scan(vec![found(ID, [192, 168, 50, 20], true)]));
        launch.session = Some(session(ID, found(ID, [192, 168, 50, 20], true), "new"));
        let addable = HashMap::from([("serial-x".to_string(), Addable::Serial(json!({})))]);
        assert!(launch
            .refreshed(ID, "old", addable.clone(), HashMap::new())
            .is_ok());
        assert!(launch.session.as_ref().unwrap().addable.is_empty());
        assert!(launch.refreshed(ID, "new", addable, HashMap::new()).is_ok());
        assert_eq!(launch.session.as_ref().unwrap().addable.len(), 1);
        // The bridge refuses the old token after the change: the new session stays.
        assert!(!launch.drop_token("old"));
        assert!(launch.session.is_some());
        assert!(launch.drop_token("new"));
        assert!(launch.session.is_none());
        assert_eq!(
            launch
                .refreshed(ID, "new", HashMap::new(), HashMap::new())
                .err()
                .as_deref(),
            Some(SESSION_EXPIRED)
        );
    }
    #[test]
    fn administration_address_is_on_the_verified_host_only() {
        // 7: the reply names another private address: Setup opens the one it verified instead.
        let mut e = found(ID, [192, 168, 50, 20], false);
        e.public = public_info(&json!({"management_endpoints": [
            {"url": "http://192.168.50.99/", "path": "home", "tls": false}]}));
        assert_eq!(admin_url(&e), "http://192.168.50.20/");
        let mut e = hotspot(ID);
        e.public = public_info(&json!({"management_endpoints": [
            {"url": "http://192.168.50.20/", "path": "home", "tls": false},
            {"url": "http://192.168.77.1/", "path": "hotspot", "tls": false}]}));
        assert_eq!(admin_url(&e), "http://192.168.77.1/");
    }

    /// One request of the fake bridge: method, path, expected body, expected If-Match, reply.
    struct Step {
        method: &'static str,
        path: String,
        body: Option<Value>,
        if_match: Option<&'static str>,
        code: u16,
        reply: Value,
    }
    fn step(method: &'static str, path: &str, reply: Value) -> Step {
        Step {
            method,
            path: path.into(),
            body: None,
            if_match: None,
            code: 200,
            reply,
        }
    }
    /// A local fake bridge answering the steps in order, one connection each; the thread
    /// panics on any other request.
    fn fake_bridge(
        steps: Vec<Step>,
    ) -> (
        Endpoint,
        reqwest::blocking::Client,
        std::thread::JoinHandle<()>,
    ) {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            for step in steps {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut b = [0u8; 4096];
                let (header_end, length) = loop {
                    let n = socket.read(&mut b).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&b[..n]);
                    if let Some(i) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&bytes[..i]).to_ascii_lowercase();
                        let len = headers
                            .lines()
                            .find_map(|l| {
                                l.strip_prefix("content-length:")
                                    .map(|s| s.trim().parse::<usize>().unwrap())
                            })
                            .unwrap_or(0);
                        break (i + 4, len);
                    }
                };
                while bytes.len() < header_end + length {
                    let n = socket.read(&mut b).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&b[..n]);
                }
                let headers = String::from_utf8_lossy(&bytes[..header_end]).to_string();
                assert!(
                    headers.starts_with(&format!("{} {} HTTP/1.1\r\n", step.method, step.path)),
                    "expected {} {}, got {}",
                    step.method,
                    step.path,
                    headers.lines().next().unwrap_or("")
                );
                let lower = headers.to_ascii_lowercase();
                assert!(lower.contains("authorization: bearer test-token"));
                match step.if_match {
                    Some(r) => assert!(
                        lower.contains(&format!("if-match: \"{r}\"")),
                        "missing If-Match {r}"
                    ),
                    None => assert!(!lower.contains("if-match:")),
                }
                if let Some(body) = step.body {
                    assert_eq!(
                        serde_json::from_slice::<Value>(&bytes[header_end..header_end + length])
                            .unwrap(),
                        body
                    );
                }
                let body = step.reply.to_string();
                write!(
                    socket,
                    "HTTP/1.1 {} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    step.code,
                    body.len(),
                    body
                )
                .unwrap();
            }
        });
        let mut e = found(ID, [127, 0, 0, 1], false);
        e.host = "127.0.0.1".into();
        e.port = port;
        let c = reqwest::blocking::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap();
        (e, c, server)
    }
    const OBSERVATION: &str = "0123456789abcdef0123456789abcdef";
    /// A bench bridge: home on the built-in radio, hotspot on wlan1, wlan2 free.
    fn bench_status(revision: &str) -> Value {
        json!({"config_revision": revision,
            "devices": [
                {"name": "wlan0", "mac": "02:00:00:00:00:00", "usb": false, "ap": true},
                {"name": "wlan1", "mac": "02:00:00:00:00:01", "usb": true, "ap": true},
                {"name": "wlan2", "mac": "02:00:00:00:00:02", "usb": true, "ap": true}],
            "hotspot": {"active": true, "mac": "02:00:00:00:00:01", "interface": "wlan1"},
            "telescopes": [],
            "config": {"home": {"ssid": "Home", "mac": "02:00:00:00:00:00"},
                "hotspot": {"enabled": true, "mac": "02:00:00:00:00:01"},
                "interface_roles": {"02:00:00:00:00:01": "hotspot"},
                "telescopes": [{"id": "scope-0a1b2c3d4e5f", "mac": "", "enabled": false}],
                "auto_connect": false}})
    }
    fn wifi_steps() -> Vec<Step> {
        let candidate = json!({"ssid": "SynScan_c0f1", "bssid": "aa:bb:cc:dd:ee:ff", "security": "",
            "suggested": {"kind": "synscan", "name": "SynScan_c0f1", "target_ip": "192.168.4.1"}});
        let mut create = step(
            "POST",
            "/api/v1/telescopes",
            json!({"telescope": {"id": "scope-0a1b2c3d4e5f", "mac": "", "enabled": false},
                "config_revision": "r1"}),
        );
        create.code = 201;
        create.body = Some(
            json!({"name": "SynScan_c0f1", "kind": "synscan", "ssid": "SynScan_c0f1",
            "target_ip": "192.168.4.1", "bssid": "aa:bb:cc:dd:ee:ff", "open": true}),
        );
        vec![
            step(
                "GET",
                &format!("/api/v1/discovery/{OBSERVATION}"),
                candidate,
            ),
            create,
        ]
    }
    fn connect_step(mac: &str, revision: &'static str, code: u16) -> Step {
        Step {
            method: "POST",
            path: "/api/v1/telescopes/scope-0a1b2c3d4e5f/connect".into(),
            body: Some(json!({"mac": mac, "expected_mac": ""})),
            if_match: Some(revision),
            code,
            reply: if code == 202 {
                json!({"ok": true, "operation_id": "op-1", "config_revision": "r3"})
            } else {
                json!({"error": {"code": "X"}})
            },
        }
    }
    #[test]
    fn an_added_wifi_mount_is_connected_on_a_free_adapter() {
        // The bridge saves the new profile inactive; Setup asks for the connection
        // on wlan2, never on the home radio nor on the radio carrying the hotspot.
        let mut steps = wifi_steps();
        steps.push(step("GET", "/api/v1/status", bench_status("r2")));
        steps.push(connect_step("02:00:00:00:00:02", "r2", 202));
        let (e, c, server) = fake_bridge(steps);
        let outcome = add_authenticated(&c, &e, "test-token", &Addable::Wifi(OBSERVATION.into()));
        server.join().unwrap();
        assert_eq!(outcome, Ok(AddOutcome::Connecting));
    }
    #[test]
    fn a_configuration_changed_meanwhile_is_read_again_once() {
        let mut steps = wifi_steps();
        steps.push(step("GET", "/api/v1/status", bench_status("r2")));
        steps.push(connect_step("02:00:00:00:00:02", "r2", 412));
        steps.push(step("GET", "/api/v1/status", bench_status("r4")));
        steps.push(connect_step("02:00:00:00:00:02", "r4", 202));
        let (e, c, server) = fake_bridge(steps);
        let outcome = add_authenticated(&c, &e, "test-token", &Addable::Wifi(OBSERVATION.into()));
        server.join().unwrap();
        assert_eq!(outcome, Ok(AddOutcome::Connecting));
        // Twice changed: the mount stays added, the page says to connect it by hand.
        let mut steps = wifi_steps();
        for revision in ["r2", "r4"] {
            steps.push(step("GET", "/api/v1/status", bench_status(revision)));
            steps.push(connect_step("02:00:00:00:00:02", revision, 412));
        }
        let (e, c, server) = fake_bridge(steps);
        let outcome = add_authenticated(&c, &e, "test-token", &Addable::Wifi(OBSERVATION.into()));
        server.join().unwrap();
        assert_eq!(outcome, Ok(AddOutcome::NotConnected));
    }
    #[test]
    fn without_a_free_adapter_the_mount_stays_added_and_nothing_is_moved() {
        let mut status = bench_status("r2");
        // wlan2 already carries another mount.
        status["config"]["telescopes"]
            .as_array_mut()
            .unwrap()
            .push(json!({"id": "scope-other", "mac": "02:00:00:00:00:02", "enabled": true}));
        let mut steps = wifi_steps();
        steps.push(step("GET", "/api/v1/status", status));
        let (e, c, server) = fake_bridge(steps);
        let outcome = add_authenticated(&c, &e, "test-token", &Addable::Wifi(OBSERVATION.into()));
        // No connect request was sent: the fake bridge had no step left for one.
        server.join().unwrap();
        assert_eq!(outcome, Ok(AddOutcome::NoFreeAdapter));
    }
    #[test]
    fn a_refused_connection_leaves_the_mount_added() {
        let mut steps = wifi_steps();
        steps.push(step("GET", "/api/v1/status", bench_status("r2")));
        steps.push(connect_step("02:00:00:00:00:02", "r2", 409));
        let (e, c, server) = fake_bridge(steps);
        let outcome = add_authenticated(&c, &e, "test-token", &Addable::Wifi(OBSERVATION.into()));
        server.join().unwrap();
        assert_eq!(outcome, Ok(AddOutcome::NotConnected));
    }
    #[test]
    fn a_serial_mount_needs_no_connection_request() {
        // serial_mounts.py: a declared mount gets its relay on the next reconcile.
        let proposal = json!({"name": "NexStar", "profile": "nexstar",
            "device": "/dev/serial/by-id/usb-1", "tcp_port": 2001});
        let mut create = step(
            "POST",
            "/api/v1/serial-mounts",
            json!({"serial_mount": {"id": "serial-1"}}),
        );
        create.code = 201;
        create.body = Some(proposal.clone());
        let (e, c, server) = fake_bridge(vec![create]);
        let outcome = add_authenticated(&c, &e, "test-token", &Addable::Serial(proposal));
        server.join().unwrap();
        assert_eq!(outcome, Ok(AddOutcome::Added));
    }
    #[test]
    fn free_adapter_follows_the_bridge_rules() {
        let status = bench_status("r");
        assert_eq!(free_adapter(&status).as_deref(), Some("02:00:00:00:00:02"));
        // A role reserved for home or the hotspot is never taken; "telescope" is.
        for (role, expected) in [
            ("home", None),
            ("hotspot", None),
            ("telescope", Some("02:00:00:00:00:02")),
            ("auto", Some("02:00:00:00:00:02")),
        ] {
            let mut s = status.clone();
            s["config"]["interface_roles"]["02:00:00:00:00:02"] = json!(role);
            assert_eq!(free_adapter(&s).as_deref(), expected, "{role}");
        }
        // The hotspot radio stays out even without a reserved role while it is active.
        let mut s = status.clone();
        s["config"]["interface_roles"] = json!({});
        s["config"]["hotspot"] = json!({"enabled": true});
        s["devices"].as_array_mut().unwrap().pop();
        assert_eq!(free_adapter(&s), None);
        // Once the hotspot is off and unreserved, its radio is free.
        s["hotspot"] = json!({"active": false, "reason": "no_free_radio"});
        assert_eq!(free_adapter(&s).as_deref(), Some("02:00:00:00:00:01"));
        // A connected mount or a wired adapter is never chosen; USB comes before built-in.
        let s = json!({"devices": [
                {"name": "eth0", "mac": "02:00:00:00:00:0e", "kind": "ethernet", "usb": false},
                {"name": "wlan0", "mac": "02:00:00:00:00:00", "usb": false},
                {"name": "wlan1", "mac": "02:00:00:00:00:01", "usb": true},
                {"name": "wlan2", "mac": "02:00:00:00:00:02", "usb": true}],
            "hotspot": {"active": false},
            "telescopes": [{"id": "a", "mac": "02:00:00:00:00:01", "connected": true}],
            "config": {"home": {}, "hotspot": {"enabled": false}, "telescopes": []}});
        assert_eq!(free_adapter(&s).as_deref(), Some("02:00:00:00:00:02"));
        let mut s = s;
        s["devices"].as_array_mut().unwrap().pop();
        assert_eq!(free_adapter(&s).as_deref(), Some("02:00:00:00:00:00"));
        // Nothing readable: no adapter.
        assert_eq!(free_adapter(&json!({})), None);
    }
    #[test]
    fn the_empty_legacy_telescope_is_not_an_added_device() {
        // GET /devices of a bridge where nothing was added.
        let devices = json!({"devices": [
            {"id": "primary", "name": "Telescope", "family": "vespera", "kind": "smart_telescope",
             "link_type": "wifi", "access": "direct", "state": "offline", "endpoint": null},
            {"id": "serial-1", "name": "NexStar", "kind": "mount", "link_type": "serial_usb",
             "access": "serial_relay", "state": "online", "endpoint": {"port": 2000}}]});
        let fresh = json!({"config": {"telescope": {"has_password": false}, "telescopes": []}});
        let names = |status: Option<&Value>| {
            added_devices(&devices, status)
                .into_iter()
                .map(|d| d.name)
                .collect::<Vec<_>>()
        };
        assert_eq!(names(Some(&fresh)), ["NexStar"]);
        // A real legacy profile (config.telescopes lists "primary") stays listed.
        let legacy = json!({"config": {"telescopes": [{"id": "primary", "enabled": true}]}});
        assert_eq!(names(Some(&legacy)), ["Telescope", "NexStar"]);
        // Without /status nothing is left out.
        assert_eq!(names(None), ["Telescope", "NexStar"]);
    }
}
