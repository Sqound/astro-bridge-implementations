mod launch;
use astro_bridge_setup_catalog::{Catalog, ReleaseArtifact, Target};
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
#[tauri::command]
async fn flash_media(
    id: String,
    confirmed: bool,
    wifi: Option<WifiSeed>,
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
        if !wifi.valid()
            || artifact.target != Target::Pi5
            || artifact.source != "local-test"
            || !artifact.wifi_seed
        {
            return Err("Wi-Fi setup is unavailable or invalid".into());
        }
        let current = astro_bridge_setup_catalog::local_candidate(&app.resources, Target::Pi5)
            .map_err(err)?
            .ok_or("Local candidate missing")?;
        if !current.wifi_seed
            || current.sha256 != artifact.sha256
            || current.size != artifact.size
            || current.tag != artifact.tag
            || current.asset != artifact.asset
        {
            return Err("Local candidate changed".into());
        }
    }
    let image = session.download.as_ref().ok_or("Download an image first")?;
    let mut request =
        serde_json::json!({"command":"flash","image":image,"sha256":artifact.sha256,"disk":disk});
    if let Some(wifi) = wifi {
        request["wifi"] = serde_json::to_value(wifi).map_err(err)?;
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
#[tauri::command]
async fn discover_bridges(
    joined_hotspot: bool,
    app: State<'_, App>,
) -> Result<Vec<launch::Bridge>, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || {
        state
            .try_lock()
            .map_err(|_| "Another bridge operation is in progress")?
            .discover(joined_hotspot)
    })
    .await
    .map_err(err)?
}
#[tauri::command]
async fn add_bridge_ip(address: String, app: State<'_, App>) -> Result<launch::Bridge, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || {
        state
            .try_lock()
            .map_err(|_| "Another bridge operation is in progress")?
            .add_manual(&address)
    })
    .await
    .map_err(err)?
}
#[tauri::command]
async fn read_bridge(
    id: String,
    password: String,
    app: State<'_, App>,
) -> Result<launch::BridgeState, String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || {
        state
            .try_lock()
            .map_err(|_| "Another bridge operation is in progress")?
            .read(&id, &password)
    })
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
        state
            .try_lock()
            .map_err(|_| "Another bridge operation is in progress")?
            .change(&id, &current_password, &new_password, &target)
    })
    .await
    .map_err(err)?
}
#[tauri::command]
async fn open_bridge_ui(id: String, app: State<'_, App>) -> Result<(), String> {
    let state = app.launch.clone();
    tauri::async_runtime::spawn_blocking(move || {
        state
            .try_lock()
            .map_err(|_| "Another bridge operation is in progress")?
            .open(&id)
    })
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
            read_bridge,
            change_bridge_password,
            open_bridge_ui
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
