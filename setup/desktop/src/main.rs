mod launch;
use astro_bridge_setup_catalog::{
    drivers::{DriverFetcher, DriverOffer, Failure},
    Catalog, ReleaseArtifact, Target,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Command, Stdio},
    str::FromStr,
};
use tauri::{ipc::Channel, Manager, State};
use tokio::sync::Mutex;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
struct Disk {
    device: String,
    name: String,
    size: u64,
    identity: String,
}
#[derive(Serialize)]
struct Media {
    id: String,
    name: String,
    size: u64,
}
#[derive(Serialize)]
struct Download {
    name: String,
}
#[derive(Clone, Serialize)]
struct Event {
    phase: String,
    message: String,
}
#[derive(Default)]
struct Session {
    release: Option<ReleaseArtifact>,
    download: Option<PathBuf>,
    media: HashMap<String, Disk>,
    /// Camera driver lines of the release being written, and the catalog version they come from.
    drivers: Option<(String, Vec<DriverOffer>)>,
    /// Verified driver archives in the cache, by manufacturer.
    driver_files: HashMap<String, PathBuf>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DriverPanel {
    supported: bool,
    offers: Vec<DriverOffer>,
}
#[derive(Clone, Serialize)]
struct DriverProgress {
    id: String,
    received: u64,
    total: u64,
}
#[derive(Serialize)]
struct DriverOutcome {
    id: String,
    /// ready, offline, failed or changed.
    outcome: String,
}
struct App {
    session: Mutex<Session>,
    launch: std::sync::Arc<std::sync::Mutex<launch::Launch>>,
    helper: PathBuf,
    cache: PathBuf,
    resources: PathBuf,
}
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn helper(
    path: &PathBuf,
    cache: &PathBuf,
    request: serde_json::Value,
    channel: Option<Channel<Event>>,
) -> Result<serde_json::Value, String> {
    let mut child = Command::new(path)
        .arg("--setup-protocol")
        .env("ASTRO_BRIDGE_SETUP_CACHE", cache.join("helper"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(err)?;
    let mut failure = None;
    if let Some(mut stdin) = child.stdin.take() {
        if let Err(e) = serde_json::to_writer(&mut stdin, &request)
            .map_err(err)
            .and_then(|_| stdin.write_all(b"\n").map_err(err))
        {
            failure = Some(e);
        }
    } else {
        failure = Some("Missing helper stdin".into());
    }
    let mut result = None;
    if let Some(stdout) = child.stdout.take() {
        for line in BufReader::new(stdout).lines() {
            match line
                .ok()
                .and_then(|line| serde_json::from_str::<serde_json::Value>(&line).ok())
            {
                Some(value) => {
                    if result.is_some() {
                        failure = Some("Message after helper outcome".into());
                    }
                    if let (Some(phase), Some(message), Some(ch)) = (
                        value.get("phase").and_then(|v| v.as_str()),
                        value.get("message").and_then(|v| v.as_str()),
                        channel.as_ref(),
                    ) {
                        if ch
                            .send(Event {
                                phase: phase.into(),
                                message: message.into(),
                            })
                            .is_err()
                        {
                            failure = Some("Progress channel closed".into());
                        }
                    } else if value.get("success").is_some()
                        || value.get("error").is_some()
                        || value.get("media").is_some()
                    {
                        if result.is_some() {
                            failure = Some("Multiple helper outcomes".into());
                        } else {
                            result = Some(value);
                        }
                    } else {
                        failure = Some("Invalid helper response".into());
                    }
                }
                None => {
                    failure = Some("Invalid helper output".into());
                }
            }
        }
    } else {
        failure = Some("Missing helper stdout".into());
    }
    let status = child.wait().map_err(err)?;
    if let Some(e) = failure {
        return Err(e);
    }
    let result = result.ok_or("Helper returned no outcome")?;
    if !status.success() || result.get("error").is_some() {
        return Err(result
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Helper failed")
            .into());
    }
    Ok(result)
}
#[tauri::command]
async fn resolve_release(
    target: String,
    app: State<'_, App>,
) -> Result<Option<ReleaseArtifact>, String> {
    let mut session = app.session.lock().await;
    session.release = None;
    session.download = None;
    session.media.clear();
    session.drivers = None;
    session.driver_files.clear();
    let target = Target::from_str(&target).map_err(err)?;
    let release =
        match astro_bridge_setup_catalog::local_candidate(&app.resources, target).map_err(err)? {
            Some(local) => Some(local),
            None => Catalog::new()
                .map_err(err)?
                .resolve(target)
                .await
                .map_err(err)?,
        };
    session.release = release.clone();
    Ok(release)
}
// Channel callbacks cause WebView work; avoid flooding it on every 64 KiB block.
// Phase changes and the final byte count are always delivered immediately.
#[derive(Default)]
struct ProgressThrottle {
    last: Option<(astro_bridge_setup_catalog::Phase, std::time::Instant, bool)>,
}
impl ProgressThrottle {
    fn should_send(
        &mut self,
        progress: astro_bridge_setup_catalog::Progress,
        now: std::time::Instant,
    ) -> bool {
        use astro_bridge_setup_catalog::Phase;
        let done = progress.total > 0 && progress.received >= progress.total;
        let send = match self.last {
            None => true,
            Some((old, time, was_done)) => {
                let same = matches!(
                    (old, progress.phase),
                    (Phase::Downloading, Phase::Downloading) | (Phase::Verifying, Phase::Verifying)
                );
                !same
                    || (done && !was_done)
                    || (!done
                        && now.saturating_duration_since(time)
                            >= std::time::Duration::from_millis(100))
            }
        };
        if send {
            self.last = Some((progress.phase, now, done));
        }
        send
    }
}
#[tauri::command]
async fn download_release(
    target: String,
    on_progress: Channel<astro_bridge_setup_catalog::Progress>,
    app: State<'_, App>,
) -> Result<Download, String> {
    let mut session = app.session.lock().await;
    session.download = None;
    let artifact = session.release.clone().ok_or("Resolve the release first")?;
    if artifact.target != Target::from_str(&target).map_err(err)? {
        return Err("Target changed; resolve again".into());
    }
    let mut throttle = ProgressThrottle::default();
    let mut report = |progress| {
        if throttle.should_send(progress, std::time::Instant::now()) {
            let _ = on_progress.send(progress);
        }
    };
    let path = if artifact.source == "local-test" {
        astro_bridge_setup_catalog::copy_local_candidate(
            &app.resources,
            &artifact,
            &app.cache,
            &mut report,
        )
        .await
        .map_err(err)?
    } else {
        Catalog::new()
            .map_err(err)?
            .download(&artifact, &app.cache, &mut report)
            .await
            .map_err(err)?
    };
    session.download = Some(path);
    Ok(Download {
        name: artifact.asset,
    })
}
#[tauri::command]
async fn list_media(app: State<'_, App>) -> Result<Vec<Media>, String> {
    let mut session = app.session.lock().await;
    session.media.clear();
    if session.download.is_none()
        || session
            .release
            .as_ref()
            .is_none_or(|r| r.target == Target::Docker)
    {
        return Err("Download a media image first".into());
    }
    let helper_path = app.helper.clone();
    let cache = app.cache.clone();
    let value = tauri::async_runtime::spawn_blocking(move || {
        helper(
            &helper_path,
            &cache,
            serde_json::json!({"command":"list"}),
            None,
        )
    })
    .await
    .map_err(err)??;
    let disks: Vec<Disk> =
        serde_json::from_value(value.get("media").cloned().ok_or("Missing media")?).map_err(err)?;
    let mut output = Vec::new();
    for disk in disks {
        if disk.identity.trim().is_empty() || disk.identity == disk.device || disk.size == 0 {
            continue;
        }
        let id = uuid::Uuid::new_v4().to_string();
        output.push(Media {
            id: id.clone(),
            name: disk.name.clone(),
            size: disk.size,
        });
        session.media.insert(id, disk);
    }
    Ok(output)
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct WifiSeed {
    ssid: String,
    password: String,
    hidden: bool,
    country: String,
}
impl WifiSeed {
    fn valid(&self) -> bool {
        let ssid = &self.ssid;
        let password = &self.password;
        !ssid.is_empty()
            && ssid.len() <= 32
            && !ssid.chars().any(char::is_control)
            && !password.chars().any(char::is_control)
            && ((8..=63).contains(&password.len())
                || (password.len() == 64 && password.bytes().all(|b| b.is_ascii_hexdigit())))
            && self.country.len() == 2
            && self.country.bytes().all(|b| b.is_ascii_uppercase())
    }
}
/// Images whose release announces the first-boot Wi-Fi slot (every published Pi 5
/// image, and any image of a format 2 manifest that says so). The helper verifies
/// the slot again in the image bytes before writing.
fn wifi_allowed(wifi: &WifiSeed, source: &str, wifi_seed: bool) -> bool {
    wifi.valid() && wifi_seed && matches!(source, "published" | "local-test")
}
/// The bridge rule: 8 to 63 printable ASCII characters without space, and not 12345678.
fn bridge_password_valid(password: &str) -> bool {
    (8..=63).contains(&password.len())
        && password.bytes().all(|b| (0x21..=0x7e).contains(&b))
        && password != "12345678"
}
#[tauri::command]
async fn driver_offers(app: State<'_, App>) -> Result<DriverPanel, String> {
    let mut session = app.session.lock().await;
    let artifact = session.release.clone().ok_or("Resolve the release first")?;
    if !artifact.driver_drop {
        return Err("This release does not carry camera drivers".into());
    }
    if !artifact.camera_service {
        session.drivers = Some((String::new(), Vec::new()));
        return Ok(DriverPanel {
            supported: false,
            offers: Vec::new(),
        });
    }
    let catalog = if artifact.source == "local-test" {
        astro_bridge_setup_catalog::drivers::local_catalog(&app.resources).map_err(err)?
    } else {
        Catalog::new()
            .map_err(err)?
            .drivers_catalog(&artifact)
            .await
            .map_err(err)?
    }
    .ok_or("Missing driver catalog")?;
    let arch = artifact
        .driver_arch
        .clone()
        .ok_or("Missing processor architecture")?;
    let offers = astro_bridge_setup_catalog::drivers::offers(&catalog, &arch);
    session.drivers = Some((catalog.catalog_version.clone(), offers.clone()));
    Ok(DriverPanel {
        supported: true,
        offers,
    })
}
#[tauri::command]
async fn download_drivers(
    ids: Vec<String>,
    on_progress: Channel<DriverProgress>,
    app: State<'_, App>,
) -> Result<Vec<DriverOutcome>, String> {
    let mut session = app.session.lock().await;
    let offers = session
        .drivers
        .as_ref()
        .ok_or("Read the camera drivers first")?
        .1
        .clone();
    let cache = app.cache.join("drivers");
    let fetcher = DriverFetcher::default();
    let mut outcomes = Vec::new();
    for id in ids {
        let offer = offers
            .iter()
            .find(|o| o.id == id)
            .ok_or("Unknown camera driver")?;
        let mut last = std::time::Instant::now() - std::time::Duration::from_secs(1);
        let report = |received: u64, total: u64| {
            let now = std::time::Instant::now();
            if received == total
                || now.duration_since(last) >= std::time::Duration::from_millis(100)
            {
                last = now;
                let _ = on_progress.send(DriverProgress {
                    id: offer.id.clone(),
                    received,
                    total,
                });
            }
        };
        let outcome = match fetcher.download(offer, &cache, report).await {
            Ok(path) => {
                session.driver_files.insert(id.clone(), path);
                "ready"
            }
            Err(Failure::Offline) => "offline",
            Err(Failure::Changed) => "changed",
            Err(Failure::Failed) => "failed",
        };
        outcomes.push(DriverOutcome {
            id,
            outcome: outcome.into(),
        });
    }
    Ok(outcomes)
}
/// Opens the official page of a manufacturer of the catalog being written; never another address.
#[tauri::command]
async fn open_licence(id: String, app: State<'_, App>) -> Result<(), String> {
    let session = app.session.lock().await;
    let offer = session
        .drivers
        .as_ref()
        .and_then(|(_, offers)| offers.iter().find(|o| o.id == id))
        .ok_or("Unknown camera driver")?;
    let url =
        astro_bridge_setup_catalog::drivers::https_url(&offer.terms_url).ok_or("Invalid page")?;
    launch::open_url(url.as_str())
}
#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn flash_media(
    id: String,
    confirmed: bool,
    wifi: Option<WifiSeed>,
    password: Option<String>,
    drivers: Option<Vec<String>>,
    on_progress: Channel<Event>,
    app: State<'_, App>,
) -> Result<(), String> {
    let mut session = app.session.lock().await;
    if !confirmed {
        return Err("Explicit erase confirmation required".into());
    }
    let disk = session
        .media
        .remove(&id)
        .ok_or("Media selection is stale")?;
    session.media.clear();
    let artifact = session.release.as_ref().ok_or("Resolve a release first")?;
    if artifact.target == Target::Docker {
        return Err("Docker cannot be flashed".into());
    }
    if let Some(ref wifi) = wifi {
        if !wifi_allowed(wifi, &artifact.source, artifact.wifi_seed) {
            return Err("Wi-Fi setup is unavailable or invalid".into());
        }
    }
    if let Some(ref password) = password {
        if !artifact.password_seed || !bridge_password_valid(password) {
            return Err("Bridge password is unavailable or invalid".into());
        }
    }
    let drivers = drivers.unwrap_or_default();
    let mut items = Vec::new();
    if !drivers.is_empty() {
        let (catalog_version, offers) = session
            .drivers
            .as_ref()
            .filter(|_| artifact.driver_drop && artifact.camera_service)
            .ok_or("Camera drivers are unavailable")?;
        for id in &drivers {
            let offer = offers
                .iter()
                .find(|o| &o.id == id)
                .ok_or("Unknown camera driver")?;
            let path = session
                .driver_files
                .get(id)
                .ok_or("Camera driver not downloaded")?;
            items.push(serde_json::json!({
                "id": offer.id, "version": offer.version, "arch": offer.arch, "source": offer.source,
                "path": path, "size": offer.size, "sha256": offer.sha256,
                "license_sha256": offer.license_sha256, "terms_url": offer.terms_url,
            }));
        }
        if items.is_empty() || catalog_version.is_empty() {
            return Err("Camera drivers are unavailable".into());
        }
        items.insert(0, serde_json::json!(catalog_version));
    }
    if wifi.is_some() || password.is_some() || !items.is_empty() {
        if artifact.source == "local-test" {
            let current =
                astro_bridge_setup_catalog::local_candidate(&app.resources, artifact.target)
                    .map_err(err)?
                    .ok_or("Local candidate missing")?;
            if (wifi.is_some() && !current.wifi_seed)
                || (password.is_some() && !current.password_seed)
                || current.sha256 != artifact.sha256
                || current.size != artifact.size
                || current.tag != artifact.tag
                || current.asset != artifact.asset
            {
                return Err("Local candidate changed".into());
            }
        }
    }
    let image = session.download.as_ref().ok_or("Download an image first")?;
    let mut request =
        serde_json::json!({"command":"flash","image":image,"sha256":artifact.sha256,"disk":disk});
    if let Some(wifi) = wifi {
        request["wifi"] = serde_json::to_value(wifi).map_err(err)?;
    }
    if let Some(password) = password {
        request["password"] = password.into();
    }
    if !items.is_empty() {
        let version = items.remove(0);
        request["drivers"] = serde_json::json!({"catalog_version": version, "items": items});
    }
    let helper_path = app.helper.clone();
    let cache = app.cache.clone();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        helper(&helper_path, &cache, request, Some(on_progress))
    })
    .await
    .map_err(err)??;
    if outcome.get("success") != Some(&serde_json::Value::Bool(true)) {
        return Err("No verified success from writing engine".into());
    }
    Ok(())
}
#[tauri::command]
async fn reveal_download(app: State<'_, App>) -> Result<(), String> {
    let session = app.session.lock().await;
    let path = session
        .download
        .as_ref()
        .ok_or("Download a release first")?;
    #[cfg(target_os = "macos")]
    let status = Command::new("/usr/bin/open").arg("-R").arg(path).status();
    #[cfg(target_os = "windows")]
    let status = Command::new("explorer.exe")
        .arg(format!("/select,{}", path.display()))
        .status();
    #[cfg(target_os = "linux")]
    let status = Command::new("xdg-open")
        .arg(path.parent().ok_or("No parent directory")?)
        .status();
    if !status.map_err(err)?.success() {
        return Err("Could not reveal download".into());
    }
    Ok(())
}
/// One search round at a time; the window starts the next one when this one ends.
static SCANNING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
#[tauri::command]
async fn discover_bridges(
    joined_hotspot: bool,
    app: State<'_, App>,
) -> Result<Vec<launch::Bridge>, String> {
    use std::sync::atomic::Ordering;
    if SCANNING.swap(true, Ordering::AcqRel) {
        return Err("A search is already running".into());
    }
    let state = app.launch.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        // The search runs without the lock: choosing, reading and adding keep working meanwhile.
        let found = launch::scan(joined_hotspot);
        launch::merge(&state, found)
    })
    .await
    .map_err(err);
    SCANNING.store(false, Ordering::Release);
    result
}
#[tauri::command]
async fn add_bridge_ip(address: String, app: State<'_, App>) -> Result<launch::Bridge, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || launch::add_manual(&state, &address))
        .await
        .map_err(err)?
}
#[tauri::command]
async fn unlock_bridge(
    id: String,
    password: String,
    app: State<'_, App>,
) -> Result<launch::BridgeDetails, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || launch::unlock(&state, &id, &password))
        .await
        .map_err(err)?
}
#[tauri::command]
async fn refresh_bridge(id: String, app: State<'_, App>) -> Result<launch::BridgeDetails, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || launch::refresh(&state, &id))
        .await
        .map_err(err)?
}
#[tauri::command]
async fn forget_bridge_session(app: State<'_, App>) -> Result<(), String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || launch::forget(&state))
        .await
        .map_err(err)
}
#[tauri::command]
async fn add_bridge_device(
    id: String,
    device: String,
    app: State<'_, App>,
) -> Result<launch::AddOutcome, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || launch::add_device(&state, &id, &device))
        .await
        .map_err(err)?
}
#[tauri::command]
async fn change_bridge_password(
    id: String,
    current_password: String,
    new_password: String,
    target: String,
    app: State<'_, App>,
) -> Result<launch::ChangeResult, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || {
        launch::change(&state, &id, &current_password, &new_password, &target)
    })
    .await
    .map_err(err)?
}
#[tauri::command]
async fn open_bridge_ui(id: String, app: State<'_, App>) -> Result<(), String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || launch::open(&state, &id))
        .await
        .map_err(err)?
}
fn main() {
    tauri::Builder::default()
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.state::<App>().session.try_lock().is_err() {
                    api.prevent_close();
                }
            }
            // The bridge token lives only as long as the window: sign out when it goes.
            // In the background: the window closes at once, the sign-out has its own short timeouts.
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<App>().launch.clone();
                std::thread::spawn(move || launch::forget(&state));
            }
        })
        .setup(|app| {
            let helper = app
                .path()
                .resource_dir()?
                .join("bin")
                .join(if cfg!(windows) {
                    "astro-bridge-helper.exe"
                } else {
                    "astro-bridge-helper"
                });
            let cache = std::env::var_os("ASTRO_BRIDGE_WORKSPACE")
                .map(|p| PathBuf::from(p).join("release-artifacts/setup-cache"))
                .unwrap_or(app.path().app_cache_dir()?.join("downloads"));
            app.manage(App {
                session: Mutex::new(Session::default()),
                launch: Default::default(),
                helper,
                cache,
                resources: app.path().resource_dir()?,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            resolve_release,
            download_release,
            list_media,
            flash_media,
            reveal_download,
            discover_bridges,
            add_bridge_ip,
            unlock_bridge,
            refresh_bridge,
            forget_bridge_session,
            add_bridge_device,
            change_bridge_password,
            open_bridge_ui,
            driver_offers,
            download_drivers,
            open_licence
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Astro Bridge Setup");
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    #[test]
    fn wifi_seed_validates_utf8_lengths_and_controls() {
        let mut wifi = WifiSeed {
            ssid: "Home".into(),
            password: "password".into(),
            hidden: false,
            country: "JP".into(),
        };
        assert!(wifi.valid());
        wifi.ssid = "あ".repeat(11);
        assert!(!wifi.valid());
        wifi.ssid = "Home".into();
        wifi.password = "a".repeat(64);
        assert!(wifi.valid());
        wifi.password = "z".repeat(64);
        assert!(!wifi.valid());
        wifi.password = "pass\nword".into();
        assert!(!wifi.valid());
        wifi.password = "password".into();
        wifi.country = "jp".into();
        assert!(!wifi.valid());
    }
    #[test]
    fn wifi_is_offered_only_for_images_that_announce_it() {
        let wifi = WifiSeed {
            ssid: "Home".into(),
            password: "password".into(),
            hidden: false,
            country: "FR".into(),
        };
        // Same panel on every hardware: the release says whether its image reads the slot.
        assert!(wifi_allowed(&wifi, "published", true));
        assert!(wifi_allowed(&wifi, "local-test", true));
        assert!(!wifi_allowed(&wifi, "local-test", false));
        assert!(!wifi_allowed(&wifi, "published", false));
        assert!(!wifi_allowed(&wifi, "other", true));
        let invalid = WifiSeed {
            country: "fr".into(),
            ..wifi
        };
        assert!(!wifi_allowed(&invalid, "published", true));
    }
    #[test]
    fn bridge_password_follows_the_bridge_rule() {
        assert!(bridge_password_valid("Synthetic-1"));
        assert!(bridge_password_valid(&"a".repeat(63)));
        for bad in [
            "short",
            "12345678",
            "with space",
            "étoile2026",
            &"a".repeat(64),
        ] {
            assert!(!bridge_password_valid(bad), "{bad}");
        }
    }
    fn fixture(body: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("setup-test-{}", uuid::Uuid::new_v4()));
        std::fs::write(&path, format!("#!/bin/sh\ncat >/dev/null\n{body}\n")).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        path
    }
    #[test]
    fn progress_is_bounded_and_keeps_phase_boundaries() {
        use astro_bridge_setup_catalog::{Phase, Progress};
        let start = std::time::Instant::now();
        let mut t = ProgressThrottle::default();
        let p = |phase, received| Progress {
            phase,
            received,
            total: 2_000,
        };
        assert!(t.should_send(p(Phase::Downloading, 0), start));
        for n in 1..2_000 {
            assert!(!t.should_send(p(Phase::Downloading, n), start));
        }
        assert!(t.should_send(p(Phase::Downloading, 2_000), start));
        assert!(!t.should_send(p(Phase::Downloading, 2_000), start));
        assert!(t.should_send(p(Phase::Verifying, 0), start));
        assert!(!t.should_send(
            p(Phase::Verifying, 1),
            start + std::time::Duration::from_millis(99)
        ));
        assert!(t.should_send(
            p(Phase::Verifying, 2),
            start + std::time::Duration::from_millis(100)
        ));
        assert!(t.should_send(
            p(Phase::Verifying, 2_000),
            start + std::time::Duration::from_millis(101)
        ));
    }
    #[test]
    fn requires_one_explicit_outcome_and_successful_exit() {
        for (body, expected) in [
            ("echo '{\"success\":true}'", true),
            ("echo '{\"success\":true}'; exit 1", false),
            (
                "echo '{\"success\":true}'; echo '{\"success\":true}'",
                false,
            ),
            ("echo broken", false),
            ("echo '{\"error\":\"cancelled\"}'; exit 1", false),
            ("exit 0", false),
        ] {
            let path = fixture(body);
            assert_eq!(
                helper(&path, &std::env::temp_dir(), serde_json::json!({}), None).is_ok(),
                expected
            );
            std::fs::remove_file(path).unwrap();
        }
    }
}
