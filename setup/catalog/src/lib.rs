//! Published release selection and verified downloads for Astro Bridge Setup.
pub mod drivers;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
    str::FromStr,
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use url::Url;

const REPO: &str = "Sqound/astro-bridge-implementations";
const MANIFEST: &str = "astro-bridge-setup-manifest.json";
/// Format 2 adds what each image reads at first boot and its camera support. Older Setup
/// releases only read format 1, which a release keeps publishing beside it.
const MANIFEST_2: &str = "astro-bridge-setup-manifest-2.json";
const MAX_METADATA: usize = 2 * 1024 * 1024;
const MAX_MANIFEST: usize = 256 * 1024;
const MAX_RELEASES: usize = 100;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("network: {0}")]
    Network(reqwest::Error),
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid metadata: {0}")]
    Invalid(String),
    #[error("JSON: {0}")]
    Json(#[from] serde_json::Error),
}
impl From<reqwest::Error> for Error {
    fn from(error: reqwest::Error) -> Self {
        Self::Network(error.without_url())
    }
}
type Result<T> = std::result::Result<T, Error>;
fn invalid(s: &str) -> Error {
    Error::Invalid(s.into())
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, Eq, PartialEq, Hash)]
#[serde(rename_all = "kebab-case")]
pub enum Target {
    PiZeroW,
    #[serde(rename = "pi-3")]
    Pi3,
    #[serde(rename = "pi-4")]
    Pi4,
    #[serde(rename = "pi-5")]
    Pi5,
    #[serde(rename = "live-usb-x86_64")]
    LiveUsbX86_64,
    #[serde(rename = "live-usb-aarch64")]
    LiveUsbAarch64,
    Docker,
}
impl FromStr for Target {
    type Err = Error;
    fn from_str(s: &str) -> Result<Self> {
        serde_json::from_value(s.into()).map_err(|_| invalid("unknown target"))
    }
}
#[derive(Clone, Debug, Serialize)]
pub struct ReleaseArtifact {
    pub target: Target,
    pub tag: String,
    pub asset: String,
    pub size: u64,
    pub sha256: String,
    #[serde(skip)]
    url: Option<Url>,
    pub source: String,
    pub wifi_seed: bool,
    /// The image reads the bridge password from the first-boot zone (version 2).
    pub password_seed: bool,
    /// The image installs camera drivers placed after it on the card.
    pub driver_drop: bool,
    /// The image runs the camera service; without it the panel says "not supported".
    pub camera_service: bool,
    pub driver_arch: Option<String>,
    #[serde(skip)]
    drivers_catalog: Option<AssetRef>,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AssetRef {
    asset: String,
    sha256: String,
    size: u64,
    #[serde(skip)]
    url: Option<Url>,
}
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Downloading,
    Verifying,
}
#[derive(Clone, Copy, Debug, Serialize)]
pub struct Progress {
    pub phase: Phase,
    pub received: u64,
    pub total: u64,
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    published_at: Option<String>,
    draft: bool,
    prerelease: bool,
    assets: Vec<Asset>,
}
#[derive(Deserialize)]
struct Asset {
    name: String,
    size: u64,
    digest: Option<String>,
    browser_download_url: String,
    state: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format: u32,
    entries: Vec<Entry>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    target: Target,
    asset: String,
    sha256: String,
    size: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest2 {
    format: u32,
    entries: Vec<Entry2>,
    drivers_catalog: Option<AssetRef>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry2 {
    target: Target,
    asset: String,
    sha256: String,
    size: u64,
    first_boot: FirstBoot,
    cameras: Cameras,
}
#[derive(Clone, Copy, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct FirstBoot {
    zone: u32,
    wifi_seed: bool,
    password_seed: bool,
    driver_drop: bool,
}
#[derive(Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cameras {
    service: bool,
    #[serde(default)]
    arch: Option<String>,
}

fn safe_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 200
        && s != "."
        && s != ".."
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
        && !s.starts_with('.')
}
fn sha_valid(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn stable_tag(s: &str) -> bool {
    let Some(v) = s.strip_prefix('v') else {
        return false;
    };
    let parts: Vec<_> = v.split('.').collect();
    parts.len() == 3
        && parts.iter().all(|p| {
            !p.is_empty()
                && p.bytes().all(|b| b.is_ascii_digit())
                && (p.len() == 1 || !p.starts_with('0'))
        })
}
fn expected_url(tag: &str, name: &str) -> Result<Url> {
    if !stable_tag(tag) || !safe_name(name) {
        return Err(invalid("unsafe tag or asset name"));
    }
    Url::parse(&format!(
        "https://github.com/{REPO}/releases/download/{tag}/{name}"
    ))
    .map_err(|_| invalid("asset URL"))
}
fn artifact(release: &Release, entry: &Entry) -> Result<ReleaseArtifact> {
    let suffix_ok = match entry.target {
        Target::Docker => entry.asset.ends_with(".sh"),
        _ => entry.asset.ends_with(".img") || entry.asset.ends_with(".img.xz"),
    };
    if !safe_name(&entry.asset) || !suffix_ok || !sha_valid(&entry.sha256) || entry.size == 0 {
        return Err(invalid("invalid manifest entry"));
    }
    let mut matches = release.assets.iter().filter(|a| a.name == entry.asset);
    let a = matches
        .next()
        .ok_or_else(|| invalid("asset absent from release"))?;
    if matches.next().is_some() || a.state != "uploaded" || a.size != entry.size {
        return Err(invalid("ambiguous or mismatched asset"));
    }
    if let Some(digest) = &a.digest {
        if digest != &format!("sha256:{}", entry.sha256) {
            return Err(invalid("GitHub digest mismatch"));
        }
    }
    let url = expected_url(&release.tag_name, &entry.asset)?;
    if a.browser_download_url != url.as_str() {
        return Err(invalid("asset URL outside release"));
    }
    Ok(ReleaseArtifact {
        target: entry.target,
        tag: release.tag_name.clone(),
        asset: entry.asset.clone(),
        size: entry.size,
        sha256: entry.sha256.clone(),
        url: Some(url),
        source: "published".into(),
        // Every published Pi 5 image (1.1.0 and later) reserves the first-boot
        // Wi-Fi slot that its first boot reads. The writing helper still checks
        // that slot in the downloaded bytes and refuses, before writing anything,
        // an image without it.
        wifi_seed: entry.target == Target::Pi5,
        password_seed: false,
        driver_drop: false,
        camera_service: false,
        driver_arch: None,
        drivers_catalog: None,
    })
}
fn first_boot_artifact(
    release: &Release,
    entry: &Entry2,
    catalog: &Option<AssetRef>,
) -> Result<ReleaseArtifact> {
    let mut found = artifact(
        release,
        &Entry {
            target: entry.target,
            asset: entry.asset.clone(),
            sha256: entry.sha256.clone(),
            size: entry.size,
        },
    )?;
    let boot = entry.first_boot;
    let arch_ok = match entry.cameras.arch.as_deref() {
        None => true,
        Some(a) => matches!(a, "arm64" | "armv6"),
    };
    // Zone 0: no first-boot zone (Docker, USB); zone 1: Wi-Fi only; zone 2: Wi-Fi, password, drivers.
    if !matches!(boot.zone, 0..=2)
        || (boot.zone < 2 && (boot.password_seed || boot.driver_drop))
        || (boot.zone == 0 && boot.wifi_seed)
        || !arch_ok
        || (boot.driver_drop
            && entry.cameras.service
            && (entry.cameras.arch.is_none() || catalog.is_none()))
    {
        return Err(invalid("invalid first-boot capabilities"));
    }
    found.wifi_seed = boot.wifi_seed;
    found.password_seed = boot.password_seed;
    found.driver_drop = boot.driver_drop;
    found.camera_service = entry.cameras.service;
    found.driver_arch = entry.cameras.arch.clone();
    found.drivers_catalog = if boot.driver_drop {
        catalog.clone()
    } else {
        None
    };
    Ok(found)
}
fn manifest2_artifact(
    release: &Release,
    bytes: &[u8],
    target: Target,
) -> Result<Option<ReleaseArtifact>> {
    let manifest: Manifest2 = serde_json::from_slice(bytes)?;
    if manifest.format != 2 || manifest.entries.len() > 100 {
        return Err(invalid("unsupported manifest format or size"));
    }
    let catalog = match manifest.drivers_catalog {
        None => None,
        Some(mut c) => {
            if !safe_name(&c.asset)
                || !c.asset.ends_with(".json")
                || !sha_valid(&c.sha256)
                || c.size == 0
                || c.size > drivers::MAX_CATALOG as u64
            {
                return Err(invalid("invalid driver catalog asset"));
            }
            let a = release
                .assets
                .iter()
                .filter(|a| a.name == c.asset)
                .collect::<Vec<_>>();
            if a.len() != 1 || a[0].state != "uploaded" || a[0].size != c.size {
                return Err(invalid("driver catalog absent or mismatched"));
            }
            let url = expected_url(&release.tag_name, &c.asset)?;
            if a[0].browser_download_url != url.as_str() {
                return Err(invalid("driver catalog URL outside release"));
            }
            c.url = Some(url);
            Some(c)
        }
    };
    let mut targets = HashSet::new();
    let mut result = None;
    for entry in &manifest.entries {
        if !targets.insert(entry.target) {
            return Err(invalid("duplicate target"));
        }
        let found = first_boot_artifact(release, entry, &catalog)?;
        if entry.target == target {
            result = Some(found);
        }
    }
    Ok(result)
}
fn manifest_artifact(
    release: &Release,
    bytes: &[u8],
    target: Target,
) -> Result<Option<ReleaseArtifact>> {
    let manifest: Manifest = serde_json::from_slice(bytes)?;
    if manifest.format != 1 || manifest.entries.len() > 100 {
        return Err(invalid("unsupported manifest format or size"));
    }
    let mut targets = HashSet::new();
    let mut assets = HashMap::new();
    let mut result = None;
    for entry in &manifest.entries {
        if !targets.insert(entry.target) {
            return Err(invalid("duplicate target"));
        }
        if let Some((size, digest)) = assets.insert(&entry.asset, (entry.size, &entry.sha256)) {
            if size != entry.size || digest != &entry.sha256 {
                return Err(invalid("conflicting asset metadata"));
            }
        }
        let found = artifact(release, entry)?;
        if entry.target == target {
            result = Some(found);
        }
    }
    Ok(result)
}
fn legacy(release: &Release, target: Target) -> Result<Option<ReleaseArtifact>> {
    if release.tag_name != "v1.0.1" {
        return Ok(None);
    }
    let (asset, size, sha256) = match target {
        Target::PiZeroW => (
            "astro-bridge-1.0.1-alpine-armhf-standard.img.xz",
            117350268,
            "8b5b2480a70472d55715c562bdfcb3ac555fe945f1205329ad08e6e4b67032ea",
        ),
        Target::Docker => (
            "install-docker.sh",
            1190,
            "07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78",
        ),
        _ => return Ok(None),
    };
    artifact(
        release,
        &Entry {
            target,
            asset: asset.into(),
            size,
            sha256: sha256.into(),
        },
    )
    .map(Some)
}
fn allowed(url: &Url) -> bool {
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port_or_known_default() == Some(443)
        && matches!(
            url.host_str(),
            Some(
                "api.github.com"
                    | "github.com"
                    | "release-assets.githubusercontent.com"
                    | "objects.githubusercontent.com"
            )
        )
}

const LOCAL_MANIFEST: &str = "pi5-candidate.json";
const LOCAL_IMAGE: &str = "astro-bridge-pi5-candidate.img.xz";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LocalManifest {
    #[serde(default)]
    wifi_seed: bool,
    #[serde(default)]
    first_boot: Option<FirstBoot>,
    #[serde(default)]
    cameras: Option<Cameras>,
    target: Target,
    tag: String,
    asset: String,
    size: u64,
    sha256: String,
}

/// A missing candidate permits the normal catalogue. A partial or invalid candidate fails closed.
pub fn local_candidate(resource_dir: &Path, target: Target) -> Result<Option<ReleaseArtifact>> {
    if target != Target::Pi5 {
        return Ok(None);
    }
    let dir = resource_dir.join("candidate");
    let manifest = dir.join(LOCAL_MANIFEST);
    let image = dir.join(LOCAL_IMAGE);
    let m = std::fs::symlink_metadata(&manifest);
    let i = std::fs::symlink_metadata(&image);
    if matches!(&m, Err(e) if e.kind() == std::io::ErrorKind::NotFound)
        && matches!(&i, Err(e) if e.kind() == std::io::ErrorKind::NotFound)
    {
        return Ok(None);
    }
    let m = m?;
    let i = i?;
    if !m.file_type().is_file() || !i.file_type().is_file() || m.len() == 0 || m.len() > 4096 {
        return Err(invalid("invalid local candidate files"));
    }
    let bytes = std::fs::read(manifest)?;
    if bytes.len() > 4096 {
        return Err(invalid("local manifest too large"));
    }
    let entry: LocalManifest = serde_json::from_slice(&bytes)?;
    if entry.target != Target::Pi5
        || entry.asset != LOCAL_IMAGE
        || entry.size == 0
        || i.len() != entry.size
        || !sha_valid(&entry.sha256)
        || entry.tag.is_empty()
        || entry.tag.len() > 100
        || entry.tag.chars().any(char::is_control)
    {
        return Err(invalid("invalid local candidate metadata"));
    }
    let boot = entry.first_boot.unwrap_or_default();
    let cameras = entry.cameras.unwrap_or_default();
    Ok(Some(ReleaseArtifact {
        target: entry.target,
        tag: entry.tag,
        asset: entry.asset,
        size: entry.size,
        sha256: entry.sha256,
        url: None,
        source: "local-test".into(),
        wifi_seed: entry.wifi_seed || boot.wifi_seed,
        password_seed: boot.password_seed,
        driver_drop: boot.driver_drop,
        camera_service: cameras.service,
        driver_arch: cameras.arch,
        drivers_catalog: None,
    }))
}

pub async fn copy_local_candidate<F: FnMut(Progress)>(
    resource_dir: &Path,
    artifact: &ReleaseArtifact,
    cache_dir: &Path,
    mut progress: F,
) -> Result<PathBuf> {
    let current = local_candidate(resource_dir, Target::Pi5)?
        .ok_or_else(|| invalid("local candidate missing"))?;
    if artifact.source != "local-test"
        || artifact.target != current.target
        || artifact.tag != current.tag
        || artifact.asset != current.asset
        || artifact.size != current.size
        || artifact.sha256 != current.sha256
        || artifact.wifi_seed != current.wifi_seed
        || artifact.password_seed != current.password_seed
        || artifact.driver_drop != current.driver_drop
    {
        return Err(invalid("local candidate changed"));
    }
    tokio::fs::create_dir_all(cache_dir).await?;
    let destination = cache_dir.join(format!("{}-{}", artifact.sha256, artifact.asset));
    let temp = tempfile::NamedTempFile::new_in(cache_dir)?;
    let mut source =
        tokio::fs::File::open(resource_dir.join("candidate").join(LOCAL_IMAGE)).await?;
    let mut output = tokio::fs::File::from_std(temp.reopen()?);
    let mut copied = 0u64;
    let mut buffer = [0u8; 65536];
    progress(Progress {
        phase: Phase::Downloading,
        received: 0,
        total: artifact.size,
    });
    loop {
        let n = source.read(&mut buffer).await?;
        if n == 0 {
            break;
        }
        copied = copied
            .checked_add(n as u64)
            .ok_or_else(|| invalid("local file overflow"))?;
        if copied > artifact.size {
            return Err(invalid("local file exceeds expected size"));
        }
        output.write_all(&buffer[..n]).await?;
        progress(Progress {
            phase: Phase::Downloading,
            received: copied,
            total: artifact.size,
        });
    }
    output.flush().await?;
    output.sync_all().await?;
    drop(output);
    let mut file = tokio::fs::File::open(temp.path()).await?;
    verify(&mut file, artifact, &mut progress).await?;
    drop(file);
    temp.persist(&destination).map_err(|e| e.error)?;
    Ok(destination)
}

pub struct Catalog {
    client: reqwest::Client,
}
impl Catalog {
    pub fn new() -> Result<Self> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .read_timeout(Duration::from_secs(60))
            .user_agent("astro-bridge-setup-catalog/0.1")
            .https_only(true)
            .redirect(reqwest::redirect::Policy::custom(|a| {
                if a.previous().len() >= 5 || !allowed(a.url()) {
                    a.error("disallowed redirect")
                } else {
                    a.follow()
                }
            }))
            .build()?;
        Ok(Self { client })
    }
    async fn get(&self, url: Url) -> Result<reqwest::Response> {
        if !allowed(&url) {
            return Err(invalid("disallowed URL"));
        }
        Ok(self.client.get(url).send().await?.error_for_status()?)
    }
    async fn bounded(&self, url: Url, limit: usize) -> Result<Vec<u8>> {
        let response = self.get(url).await?;
        if response.content_length().is_some_and(|n| n > limit as u64) {
            return Err(invalid("metadata too large"));
        }
        let mut stream = response.bytes_stream();
        let mut out = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            if out.len() + chunk.len() > limit {
                return Err(invalid("metadata too large"));
            }
            out.extend_from_slice(&chunk);
        }
        Ok(out)
    }
    pub async fn resolve(&self, target: Target) -> Result<Option<ReleaseArtifact>> {
        let url = Url::parse(&format!(
            "https://api.github.com/repos/{REPO}/releases?per_page={MAX_RELEASES}&page=1"
        ))
        .unwrap();
        let mut releases: Vec<Release> =
            serde_json::from_slice(&self.bounded(url, MAX_METADATA).await?)?;
        if releases.len() > MAX_RELEASES {
            return Err(invalid("too many releases"));
        }
        releases.retain(|r| {
            !r.draft && !r.prerelease && r.published_at.is_some() && stable_tag(&r.tag_name)
        });
        releases.sort_by(|a, b| {
            b.published_at
                .cmp(&a.published_at)
                .then_with(|| b.tag_name.cmp(&a.tag_name))
        });
        for release in releases {
            if let Some(found) = self.manifest2(&release, target).await? {
                return Ok(found);
            }
            let manifests: Vec<_> = release
                .assets
                .iter()
                .filter(|a| a.name == MANIFEST)
                .collect();
            if manifests.len() > 1 {
                return Err(invalid("duplicate manifest"));
            }
            if let Some(m) = manifests.first() {
                if m.state != "uploaded" || m.size == 0 || m.size > MAX_MANIFEST as u64 {
                    return Err(invalid("invalid manifest asset"));
                }
                let url = expected_url(&release.tag_name, MANIFEST)?;
                if m.browser_download_url != url.as_str() {
                    return Err(invalid("manifest URL outside release"));
                }
                let bytes = self.bounded(url, MAX_MANIFEST).await?;
                if bytes.len() as u64 != m.size {
                    return Err(invalid("manifest size mismatch"));
                }
                if let Some(digest) = &m.digest {
                    if digest != &format!("sha256:{:x}", Sha256::digest(&bytes)) {
                        return Err(invalid("manifest digest mismatch"));
                    }
                }
                if let Some(found) = manifest_artifact(&release, &bytes, target)? {
                    return Ok(Some(found));
                }
            } else if let Some(found) = legacy(&release, target)? {
                return Ok(Some(found));
            }
        }
        Ok(None)
    }
    /// Some(result) when the release publishes the format 2 manifest; None to read format 1.
    async fn manifest2(
        &self,
        release: &Release,
        target: Target,
    ) -> Result<Option<Option<ReleaseArtifact>>> {
        let manifests: Vec<_> = release
            .assets
            .iter()
            .filter(|a| a.name == MANIFEST_2)
            .collect();
        if manifests.len() > 1 {
            return Err(invalid("duplicate manifest"));
        }
        let Some(m) = manifests.first() else {
            return Ok(None);
        };
        if m.state != "uploaded" || m.size == 0 || m.size > MAX_MANIFEST as u64 {
            return Err(invalid("invalid manifest asset"));
        }
        let url = expected_url(&release.tag_name, MANIFEST_2)?;
        if m.browser_download_url != url.as_str() {
            return Err(invalid("manifest URL outside release"));
        }
        let bytes = self.bounded(url, MAX_MANIFEST).await?;
        if bytes.len() as u64 != m.size {
            return Err(invalid("manifest size mismatch"));
        }
        if let Some(digest) = &m.digest {
            if digest != &format!("sha256:{:x}", Sha256::digest(&bytes)) {
                return Err(invalid("manifest digest mismatch"));
            }
        }
        manifest2_artifact(release, &bytes, target).map(Some)
    }
    /// The driver catalog published with this artifact's release, checked by size and SHA-256.
    pub async fn drivers_catalog(
        &self,
        artifact: &ReleaseArtifact,
    ) -> Result<Option<drivers::DriversCatalog>> {
        let Some(asset) = &artifact.drivers_catalog else {
            return Ok(None);
        };
        let url = asset.url.clone().ok_or_else(|| invalid("missing URL"))?;
        if url != expected_url(&artifact.tag, &asset.asset)? {
            return Err(invalid("driver catalog URL outside release"));
        }
        let bytes = self.bounded(url, drivers::MAX_CATALOG).await?;
        if bytes.len() as u64 != asset.size
            || format!("{:x}", Sha256::digest(&bytes)) != asset.sha256
        {
            return Err(invalid("driver catalog integrity mismatch"));
        }
        drivers::parse_catalog(&bytes).map(Some)
    }
    pub async fn download<F: FnMut(Progress)>(
        &self,
        artifact: &ReleaseArtifact,
        cache_dir: &Path,
        mut progress: F,
    ) -> Result<PathBuf> {
        // Private URL and constructor ensure callers cannot supply arbitrary download locations.
        if artifact.url.as_ref() != Some(&expected_url(&artifact.tag, &artifact.asset)?)
            || !sha_valid(&artifact.sha256)
            || artifact.size == 0
        {
            return Err(invalid("invalid artifact"));
        }
        tokio::fs::create_dir_all(cache_dir).await?;
        let destination = cache_dir.join(format!("{}-{}", artifact.sha256, artifact.asset));
        if tokio::fs::symlink_metadata(&destination)
            .await
            .is_ok_and(|m| m.file_type().is_file())
        {
            let mut file = tokio::fs::File::open(&destination).await?;
            if verify(&mut file, artifact, &mut progress).await.is_ok() {
                return Ok(destination);
            }
        }
        let temp = tempfile::NamedTempFile::new_in(cache_dir)?;
        let mut file = tokio::fs::File::from_std(temp.reopen()?);
        let response = self
            .get(artifact.url.clone().ok_or_else(|| invalid("missing URL"))?)
            .await?;
        if response
            .content_length()
            .is_some_and(|n| n != artifact.size)
        {
            return Err(invalid("download size mismatch"));
        }
        let mut stream = response.bytes_stream();
        let mut received = 0u64;
        progress(Progress {
            phase: Phase::Downloading,
            received,
            total: artifact.size,
        });
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            received = received
                .checked_add(chunk.len() as u64)
                .ok_or_else(|| invalid("download overflow"))?;
            if received > artifact.size {
                return Err(invalid("download exceeds expected size"));
            }
            file.write_all(&chunk).await?;
            progress(Progress {
                phase: Phase::Downloading,
                received,
                total: artifact.size,
            });
        }
        file.flush().await?;
        file.sync_all().await?;
        drop(file);
        let mut file = tokio::fs::File::open(temp.path()).await?;
        verify(&mut file, artifact, &mut progress).await?;
        drop(file);
        temp.persist(&destination).map_err(|e| e.error)?;
        Ok(destination)
    }
}
async fn verify<F: FnMut(Progress)>(
    file: &mut tokio::fs::File,
    artifact: &ReleaseArtifact,
    progress: &mut F,
) -> Result<()> {
    let mut hash = Sha256::new();
    let mut size = 0u64;
    let mut buf = [0u8; 65536];
    progress(Progress {
        phase: Phase::Verifying,
        received: 0,
        total: artifact.size,
    });
    loop {
        let n = file.read(&mut buf).await?;
        if n == 0 {
            break;
        }
        size += n as u64;
        if size > artifact.size {
            return Err(invalid("file exceeds expected size"));
        }
        hash.update(&buf[..n]);
        progress(Progress {
            phase: Phase::Verifying,
            received: size,
            total: artifact.size,
        });
    }
    if size != artifact.size || format!("{:x}", hash.finalize()) != artifact.sha256 {
        return Err(invalid("file integrity mismatch"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn release() -> Release {
        Release { tag_name: "v1.0.1".into(), published_at: Some("2026-01-01T00:00:00Z".into()), draft: false, prerelease: false, assets: vec![Asset { name: "install-docker.sh".into(), size: 1190, digest: Some("sha256:07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78".into()), browser_download_url: "https://github.com/Sqound/astro-bridge-implementations/releases/download/v1.0.1/install-docker.sh".into(), state: "uploaded".into() }] }
    }
    #[test]
    fn local_candidate_fails_closed_and_verifies_copy() {
        let dir = tempfile::tempdir().unwrap();
        let cache = tempfile::tempdir().unwrap();
        let candidate = dir.path().join("candidate");
        assert!(local_candidate(dir.path(), Target::Pi5).unwrap().is_none());
        std::fs::create_dir(&candidate).unwrap();
        let image = candidate.join(LOCAL_IMAGE);
        std::fs::write(&image, b"abc").unwrap();
        assert!(local_candidate(dir.path(), Target::Pi5).is_err());
        assert!(local_candidate(dir.path(), Target::Pi4).unwrap().is_none());
        let manifest = candidate.join(LOCAL_MANIFEST);
        let good = format!(
            r#"{{"target":"pi-5","tag":"test","asset":"{}","size":3,"sha256":"{:x}"}}"#,
            LOCAL_IMAGE,
            Sha256::digest(b"abc")
        );
        std::fs::write(&manifest, &good).unwrap();
        let old = local_candidate(dir.path(), Target::Pi5).unwrap().unwrap();
        assert!(!old.wifi_seed);
        std::fs::write(
            &manifest,
            good.replace("\"size\":3", "\"wifi_seed\":true,\"size\":3"),
        )
        .unwrap();
        let artifact = local_candidate(dir.path(), Target::Pi5).unwrap().unwrap();
        assert!(artifact.wifi_seed);
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        rt.block_on(async {
            assert!(copy_local_candidate(dir.path(), &old, cache.path(), |_| {})
                .await
                .is_err());
            let path = copy_local_candidate(dir.path(), &artifact, cache.path(), |_| {})
                .await
                .unwrap();
            assert_eq!(std::fs::read(path).unwrap(), b"abc");
            std::fs::write(&image, b"xyz").unwrap();
            assert!(
                copy_local_candidate(dir.path(), &artifact, cache.path(), |_| {})
                    .await
                    .is_err()
            );
        });
        for bad in [
            good.replace("pi-5", "pi-4"),
            good.replace(LOCAL_IMAGE, "../bad.img.xz"),
            good.replace("\"size\":3", "\"size\":4"),
        ] {
            std::fs::write(&manifest, bad).unwrap();
            assert!(local_candidate(dir.path(), Target::Pi5).is_err());
        }
    }
    #[test]
    fn legacy_is_exact_and_targets_do_not_substitute() {
        let mut r = release();
        assert_eq!(legacy(&r, Target::Docker).unwrap().unwrap().size, 1190);
        assert!(legacy(&r, Target::Pi5).unwrap().is_none());
        r.assets[0].digest = Some("sha256:bad".into());
        assert!(legacy(&r, Target::Docker).is_err());
        r.tag_name = "v1.0.2".into();
        assert!(legacy(&r, Target::Docker).unwrap().is_none());
    }
    #[test]
    fn manifest_rejects_ambiguity_and_corruption() {
        let r = release();
        let good = r#"{"format":1,"entries":[{"target":"docker","asset":"install-docker.sh","size":1190,"sha256":"07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78"}]}"#;
        assert!(manifest_artifact(&r, good.as_bytes(), Target::Docker)
            .unwrap()
            .is_some());
        assert!(manifest_artifact(&r, good.as_bytes(), Target::Pi3)
            .unwrap()
            .is_none());
        for bad in [good.replace("1190", "1191"), good.replace("install-docker.sh", "../install-docker.sh"), good.replace("\"format\":1", "\"format\":2"), good.replace("\"target\":\"docker\"", "\"target\":\"unknown\""), good.replace("\"entries\":[", "\"entries\":[{\"target\":\"docker\",\"asset\":\"install-docker.sh\",\"size\":1190,\"sha256\":\"07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78\"},")] { assert!(manifest_artifact(&r, bad.as_bytes(), Target::Docker).is_err()); }
        let mut r = release();
        r.assets.push(Asset {
            name: r.assets[0].name.clone(),
            size: 1190,
            digest: None,
            browser_download_url: r.assets[0].browser_download_url.clone(),
            state: "uploaded".into(),
        });
        assert!(manifest_artifact(&r, good.as_bytes(), Target::Docker).is_err());
    }
    #[test]
    fn shared_images_require_explicit_matching_entries_and_correct_suffixes() {
        let mut r = release();
        r.assets[0].name = "shared.img.xz".into();
        r.assets[0].browser_download_url =
            expected_url("v1.0.1", "shared.img.xz").unwrap().to_string();
        let entries = r#"{"format":1,"entries":[{"target":"pi-3","asset":"shared.img.xz","size":1190,"sha256":"07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78"},{"target":"pi-4","asset":"shared.img.xz","size":1190,"sha256":"07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78"}]}"#;
        assert!(manifest_artifact(&r, entries.as_bytes(), Target::Pi3)
            .unwrap()
            .is_some());
        assert!(manifest_artifact(&r, entries.as_bytes(), Target::Pi4)
            .unwrap()
            .is_some());
        assert!(manifest_artifact(&r, entries.as_bytes(), Target::Pi5)
            .unwrap()
            .is_none());
        assert!(
            !manifest_artifact(&r, entries.as_bytes(), Target::Pi3)
                .unwrap()
                .unwrap()
                .wifi_seed
        );
        let pi5 = manifest_artifact(&r, entries.replace("pi-4", "pi-5").as_bytes(), Target::Pi5)
            .unwrap()
            .unwrap();
        assert!(pi5.wifi_seed && pi5.source == "published");
        assert!(
            manifest_artifact(&r, entries.replace("pi-4", "pi-3").as_bytes(), Target::Pi3).is_err()
        );
        assert!(manifest_artifact(
            &r,
            entries.replacen("1190", "1191", 1).as_bytes(),
            Target::Pi3
        )
        .is_err());
        assert!(manifest_artifact(
            &r,
            entries.replacen("07d4685d", "17d4685d", 1).as_bytes(),
            Target::Pi3
        )
        .is_err());
        assert!(manifest_artifact(
            &r,
            entries.replace("pi-4", "docker").as_bytes(),
            Target::Pi3
        )
        .is_err());
        let docker = release();
        let bad = r#"{"format":1,"entries":[{"target":"pi-3","asset":"install-docker.sh","size":1190,"sha256":"07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78"}]}"#;
        assert!(manifest_artifact(&docker, bad.as_bytes(), Target::Pi3).is_err());
    }
    #[test]
    fn format_2_manifest_announces_what_each_image_reads() {
        let mut r = release();
        for (name, size) in [
            ("pi5.img.xz", 1190u64),
            ("zero.img.xz", 1190),
            ("drivers-catalog.json", 100),
        ] {
            r.assets.push(Asset {
                name: name.into(),
                size,
                digest: None,
                browser_download_url: expected_url("v1.0.1", name).unwrap().to_string(),
                state: "uploaded".into(),
            });
        }
        let sha = "07d4685d05065320492653bcbb0de2580c03f8800fb7a15499ded301c529db78";
        let catalog = format!(
            r#"{{"asset":"drivers-catalog.json","sha256":"{}","size":100}}"#,
            "a".repeat(64)
        );
        let good = format!(
            r#"{{"format":2,"drivers_catalog":{catalog},"entries":[
            {{"target":"pi-5","asset":"pi5.img.xz","sha256":"{sha}","size":1190,"first_boot":{{"zone":2,"wifi_seed":true,"password_seed":true,"driver_drop":true}},"cameras":{{"service":true,"arch":"arm64"}}}},
            {{"target":"pi-zero-w","asset":"zero.img.xz","sha256":"{sha}","size":1190,"first_boot":{{"zone":2,"wifi_seed":true,"password_seed":true,"driver_drop":true}},"cameras":{{"service":false}}}}]}}"#
        );
        let pi5 = manifest2_artifact(&r, good.as_bytes(), Target::Pi5)
            .unwrap()
            .unwrap();
        assert!(pi5.wifi_seed && pi5.password_seed && pi5.driver_drop && pi5.camera_service);
        assert_eq!(pi5.driver_arch.as_deref(), Some("arm64"));
        assert!(pi5.drivers_catalog.as_ref().unwrap().url.is_some());
        let zero = manifest2_artifact(&r, good.as_bytes(), Target::PiZeroW)
            .unwrap()
            .unwrap();
        assert!(zero.wifi_seed && zero.password_seed && !zero.camera_service);
        let json = serde_json::to_value(&zero).unwrap();
        assert!(
            json.get("drivers_catalog").is_none()
                && json.get("password_seed") == Some(&serde_json::Value::Bool(true))
        );
        for bad in [
            good.replace(
                "\"zone\":2,\"wifi_seed\":true,\"password_seed\":true",
                "\"zone\":1,\"wifi_seed\":true,\"password_seed\":true",
            ),
            good.replace("\"arch\":\"arm64\"", "\"arch\":\"x86\""),
            good.replace("drivers-catalog.json", "../catalog.json"),
            good.replace("\"size\":100", "\"size\":101"),
            good.replace("\"format\":2", "\"format\":1"),
            good.replace(
                "\"cameras\":{\"service\":false}",
                "\"cameras\":{\"service\":false,\"other\":1}",
            ),
        ] {
            assert!(
                manifest2_artifact(&r, bad.as_bytes(), Target::Pi5).is_err(),
                "{bad}"
            );
        }
        // Without a driver catalog a camera image cannot offer drivers.
        let no_catalog = good.replace(&format!("\"drivers_catalog\":{catalog},"), "");
        assert!(manifest2_artifact(&r, no_catalog.as_bytes(), Target::Pi5).is_err());
        // Format 1 releases keep the Pi 5 Wi-Fi rule and nothing else.
        let old = legacy(&release(), Target::Docker).unwrap().unwrap();
        assert!(!old.password_seed && !old.driver_drop && !old.camera_service);
    }
    #[test]
    fn network_error_redacts_url() {
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        rt.block_on(async {
            let error = reqwest::Client::new()
                .get("http://127.0.0.1:1/?secret=signed-token")
                .send()
                .await
                .unwrap_err();
            let error: Error = error.into();
            assert!(!error.to_string().contains("signed-token"));
            assert!(!format!("{error:?}").contains("signed-token"));
        });
    }
    #[test]
    fn transport_and_names_are_restricted() {
        for s in [
            "http://github.com/a",
            "https://github.com.evil.test/a",
            "https://github.com:444/a",
            "https://user@github.com/a",
        ] {
            assert!(!allowed(&Url::parse(s).unwrap()));
        }
        assert!(allowed(
            &Url::parse("https://release-assets.githubusercontent.com/x").unwrap()
        ));
        for s in ["../x", ".hidden", "x/y", "x\\y", "x y"] {
            assert!(!safe_name(s));
        }
        assert!(!stable_tag("v1.0.1-rc1"));
        assert!(stable_tag("v1.0.1"));
    }
    #[test]
    fn integrity_rejects_corruption_and_short_files() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("x");
        std::fs::write(&path, b"abc").unwrap();
        let mut a = legacy(&release(), Target::Docker).unwrap().unwrap();
        a.size = 3;
        a.sha256 = format!("{:x}", Sha256::digest(b"abc"));
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let mut file = tokio::fs::File::open(&path).await.unwrap();
            assert!(verify(&mut file, &a, &mut |_| {}).await.is_ok());
            a.sha256 = "0".repeat(64);
            let mut file = tokio::fs::File::open(&path).await.unwrap();
            assert!(verify(&mut file, &a, &mut |_| {}).await.is_err());
            a.size = 4;
            let mut file = tokio::fs::File::open(&path).await.unwrap();
            assert!(verify(&mut file, &a, &mut |_| {}).await.is_err());
        });
    }
}
