use mdns_sd::{ServiceDaemon, ServiceEvent};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket},
    time::{Duration, Instant},
};

const SERVICE: &str = "_astrobridge._tcp.local.";
const HOTSPOT: Ipv4Addr = Ipv4Addr::new(192, 168, 77, 1);
const LIMIT: usize = 65536;

#[derive(Clone)]
struct Endpoint {
    host: String,
    ip: Ipv4Addr,
    port: u16,
    secure: bool,
    hotspot: bool,
    legacy: bool,
    identity: Identity,
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
    id: String,
    name: String,
    version: String,
    path: String,
    auth_mode: String,
    ssh_host: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeState {
    home_ssid: String,
    home_connected: bool,
    hotspot_ssid: String,
    hotspot_active: bool,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChangeResult {
    sessions_invalidated: bool,
    hotspot_reconnect: bool,
}
#[derive(Default)]
pub struct Launch {
    endpoints: HashMap<String, Endpoint>,
}

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
fn request(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    method: reqwest::Method,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> Result<Value, String> {
    let mut req = c.request(method, format!("{}{path}", origin(e)));
    if let Some(t) = token {
        req = req.bearer_auth(t);
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
    if response.content_length().is_some_and(|n| n > LIMIT as u64) {
        return Err("Bridge response too large".into());
    }
    let mut bytes = Vec::new();
    std::io::Read::take(response, LIMIT as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Cannot read bridge response".to_string())?;
    if bytes.len() > LIMIT {
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
    Ok(())
}
fn auth(e: &Endpoint, password: &str) -> Result<(reqwest::blocking::Client, String), String> {
    if e.legacy {
        return Err("This legacy bridge supports browser access only".into());
    }
    if !e.secure && !(e.hotspot && e.identity.path == "hotspot" && direct_hotspot()) {
        return Err("Trusted HTTPS or a confirmed direct hotspot is required".into());
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
impl Launch {
    pub fn discover(&mut self, joined: bool) -> Result<Vec<Bridge>, String> {
        self.endpoints.clear();
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
                },
                None,
                String::new(),
                None,
            ));
        }
        // Probe the explicitly confirmed hotspot first; stop starting probes after the deadline.
        candidates.sort_by(|a, b| (!a.0.hotspot, a.0.ip, &a.2).cmp(&(!b.0.hotspot, b.0.ip, &b.2)));
        let deadline = Instant::now() + Duration::from_secs(8);
        let mut verified: HashMap<String, Endpoint> = HashMap::new();
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
                let Some(mut actual): Option<Vec<&str>> =
                    actual.iter().map(Value::as_str).collect()
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
            match verified.get(&e.identity.id) {
                Some(old) if preference(old) <= preference(&e) => {}
                _ => {
                    verified.insert(e.identity.id.clone(), e);
                }
            }
        }
        let mut selected: Vec<_> = verified.into_values().collect();
        selected.sort_by(|a, b| a.identity.id.cmp(&b.identity.id));
        let mut output = Vec::new();
        for e in selected {
            let id = uuid::Uuid::new_v4().to_string();
            let auth_mode = if e.legacy {
                "none"
            } else if e.secure {
                "https"
            } else if e.hotspot {
                "hotspot"
            } else {
                "none"
            };
            output.push(Bridge {
                id: id.clone(),
                name: e.identity.name.clone(),
                version: e.identity.version.clone(),
                path: e.identity.path.clone(),
                auth_mode: auth_mode.into(),
                ssh_host: e.ip.to_string(),
            });
            self.endpoints.insert(id, e);
        }
        Ok(output)
    }
    pub fn add_manual(&mut self, address: &str) -> Result<Bridge, String> {
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
        };
        let c = client(&endpoint)?;
        let value = request(
            &c,
            &endpoint,
            reqwest::Method::GET,
            "/api/v1/info",
            None,
            None,
        )?;
        endpoint.identity = manual_identity(&value)?;
        if self.endpoints.len() >= 64 && !self.endpoints.values().any(|old| old.ip == ip) {
            return Err("Too many bridges; search again".into());
        }
        self.endpoints.retain(|_, old| old.ip != ip);
        let id = uuid::Uuid::new_v4().to_string();
        let bridge = Bridge {
            id: id.clone(),
            name: endpoint.identity.name.clone(),
            version: endpoint.identity.version.clone(),
            path: endpoint.identity.path.clone(),
            auth_mode: "none".into(),
            ssh_host: ip.to_string(),
        };
        self.endpoints.insert(id, endpoint);
        Ok(bridge)
    }
    fn endpoint(&self, id: &str) -> Result<&Endpoint, String> {
        self.endpoints
            .get(id)
            .ok_or_else(|| "Bridge selection is stale".into())
    }
    pub fn read(&self, id: &str, password: &str) -> Result<BridgeState, String> {
        let e = self.endpoint(id)?;
        let (c, t) = auth(e, password)?;
        let result = read_authenticated(&c, e, &t);
        revoke(&c, e, &t);
        result
    }
    pub fn change(
        &self,
        id: &str,
        current: &str,
        new: &str,
        target: &str,
    ) -> Result<ChangeResult, String> {
        if !matches!(target, "web" | "hotspot") {
            return Err("Unsupported password target".into());
        }
        let e = self.endpoint(id)?;
        let (c, t) = auth(e, current)?;
        let result = change_authenticated(&c, e, &t, current, new, target);
        revoke(&c, e, &t);
        result
    }
    pub fn open(&self, id: &str) -> Result<(), String> {
        let e = self.endpoint(id)?;
        let c = client(e)?;
        verify(&c, e)?;
        let url = origin(e);
        #[cfg(target_os = "macos")]
        let status = std::process::Command::new("/usr/bin/open")
            .arg(&url)
            .status();
        #[cfg(target_os = "windows")]
        let status = std::process::Command::new("rundll32.exe")
            .arg("url.dll,FileProtocolHandler")
            .arg(&url)
            .status();
        #[cfg(target_os = "linux")]
        let status = std::process::Command::new("xdg-open").arg(&url).status();
        if status.map_err(|_| "Cannot open browser")?.success() {
            Ok(())
        } else {
            Err("Cannot open browser".into())
        }
    }
}
fn read_authenticated(
    c: &reqwest::blocking::Client,
    e: &Endpoint,
    t: &str,
) -> Result<BridgeState, String> {
    let result = (|| {
        let home = request(c, e, reqwest::Method::GET, "/api/v1/home", Some(t), None)?;
        let hot = request(
            &c,
            e,
            reqwest::Method::GET,
            "/api/v1/hotspot",
            Some(t),
            None,
        )?;
        Ok(BridgeState {
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
        })
    })();
    result
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
        })
    })();
    result
}
#[cfg(test)]
mod tests {
    use super::*;
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
        let mut launch = Launch::default();
        assert!(launch.add_manual("8.8.8.8").is_err());
        assert!(launch.endpoints.is_empty());
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
                    json!({"target":"hotspot","current_password":"test-current","password":"test-new"}),
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
        };
        let c = reqwest::blocking::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap();
        let t = login(&c, &e, "test-current").unwrap();
        let state = read_authenticated(&c, &e, &t).unwrap();
        assert_eq!(state.home_ssid, "Home");
        assert!(state.home_connected);
        assert_eq!(state.hotspot_ssid, "Bridge AP");
        assert!(state.hotspot_active);
        revoke(&c, &e, &t);
        let t = login(&c, &e, "test-current").unwrap();
        let change =
            change_authenticated(&c, &e, &t, "test-current", "test-new", "hotspot").unwrap();
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
}
