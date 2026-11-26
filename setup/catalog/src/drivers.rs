//! Camera driver catalog of the release Setup writes, and verified driver downloads.
//!
//! The catalog is the bridge's own file (`wifi_bridge/static/drivers-catalog.json`),
//! published with each release; the bridge and Setup therefore read the same
//! fingerprints. A manufacturer is offered only when every fingerprint of a build
//! for the image's processor is known. Downloads go over HTTPS to the catalog's
//! host only (redirects only to the hosts the catalog lists), are bounded by the
//! catalog size and kept only when their SHA-256 matches. Nothing is unpacked or
//! executed: the archive goes on the card unchanged.
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::io::AsyncWriteExt;
use url::Url;

use crate::{invalid, sha_valid, Error, Result};

pub const MAX_CATALOG: usize = 64 * 1024;
const MAX_ARCHIVE: u64 = 512 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DriversCatalog {
    pub schema_version: u32,
    pub catalog_version: String,
    pub drivers: Vec<DriverEntry>,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DriverEntry {
    pub id: String,
    pub name: String,
    pub verified_on_hardware: bool,
    pub terms_url: String,
    pub usb_vendors: Vec<u32>,
    pub indi_driver: Option<String>,
    pub needs_firmware: bool,
    pub builds: Vec<DriverBuild>,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DriverBuild {
    pub version: Option<String>,
    pub arch: String,
    pub source: String,
    pub url: Option<String>,
    pub redirect_hosts: Vec<String>,
    pub size: Option<u64>,
    pub sha256: Option<String>,
    pub nested: Option<String>,
    pub files: Vec<DriverFile>,
    pub license_member: Option<String>,
    pub license_sha256: Option<String>,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DriverFile {
    pub member: String,
    pub sha256: Option<String>,
    pub install_as: String,
}

/// One line of the Setup panel: a manufacturer that can be placed on the card.
#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct DriverOffer {
    pub id: String,
    pub name: String,
    pub verified_on_hardware: bool,
    pub terms_url: String,
    /// Always "download": no manufacturer file is ever part of an image.
    pub source: String,
    pub version: String,
    pub arch: String,
    pub size: Option<u64>,
    #[serde(skip)]
    pub sha256: Option<String>,
    #[serde(skip)]
    pub license_sha256: Option<String>,
    #[serde(skip)]
    url: Option<Url>,
    #[serde(skip)]
    redirect_hosts: Vec<String>,
}

fn ident(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 32
        && b[0].is_ascii_lowercase()
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_')
}
fn host(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 253
        && s.contains('.')
        && s.split('.').all(|label| {
            !label.is_empty()
                && label.len() <= 63
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
        })
}
/// HTTPS on port 443, a public host name, no credentials, no fragment.
pub fn https_url(value: &str) -> Option<Url> {
    if value.len() > 512 || value.chars().any(|c| c.is_control() || c == ' ') {
        return None;
    }
    let url = Url::parse(value).ok()?;
    (url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port_or_known_default() == Some(443)
        && url.fragment().is_none()
        && url.host_str().is_some_and(host))
    .then_some(url)
}

pub fn parse_catalog(bytes: &[u8]) -> Result<DriversCatalog> {
    if bytes.len() > MAX_CATALOG {
        return Err(invalid("driver catalog too large"));
    }
    let value: DriversCatalog = serde_json::from_slice(bytes)?;
    if value.schema_version != 1
        || value.catalog_version.len() > 32
        || value.drivers.is_empty()
        || value.drivers.len() > 32
    {
        return Err(invalid("unsupported driver catalog"));
    }
    let mut ids = HashSet::new();
    for entry in &value.drivers {
        if !ident(&entry.id)
            || !ids.insert(entry.id.clone())
            || entry.name.trim().is_empty()
            || entry.name.chars().count() > 40
            || entry.name.chars().any(char::is_control)
            || https_url(&entry.terms_url).is_none()
            || entry.builds.len() > 16
        {
            return Err(invalid("invalid driver entry"));
        }
        for build in &entry.builds {
            let fingerprints = build
                .sha256
                .iter()
                .chain(build.license_sha256.iter())
                .chain(build.files.iter().filter_map(|f| f.sha256.as_ref()));
            if !matches!(build.arch.as_str(), "arm64" | "armv6")
                || build.source != "download"
                || build.redirect_hosts.len() > 4
                || build.redirect_hosts.iter().any(|h| !host(h))
                || build.size.is_some_and(|s| s == 0 || s > MAX_ARCHIVE)
                || build.files.len() > 8
                || fingerprints.into_iter().any(|s| !sha_valid(s))
                || build.url.as_deref().and_then(https_url).is_none()
            {
                return Err(invalid("invalid driver build"));
            }
        }
    }
    Ok(value)
}

/// Every fingerprint is known: the archive (downloads), each file, and the licence text if any.
fn installable(build: &DriverBuild) -> bool {
    let files = !build.files.is_empty() && build.files.iter().all(|f| f.sha256.is_some());
    let licence = build.license_member.is_none() || build.license_sha256.is_some();
    build.version.is_some() && files && licence && build.size.is_some() && build.sha256.is_some()
}

/// The panel lines for an image: arch is its processor. Every line is a download.
pub fn offers(catalog: &DriversCatalog, arch: &str) -> Vec<DriverOffer> {
    let mut result = Vec::new();
    for entry in &catalog.drivers {
        let Some(build) = entry
            .builds
            .iter()
            .find(|b| b.arch == arch && installable(b))
        else {
            continue;
        };
        let url = build.url.as_deref().and_then(https_url);
        result.push(DriverOffer {
            id: entry.id.clone(),
            name: entry.name.clone(),
            verified_on_hardware: entry.verified_on_hardware,
            terms_url: entry.terms_url.clone(),
            source: build.source.clone(),
            version: build.version.clone().unwrap_or_default(),
            arch: build.arch.clone(),
            size: build.size,
            sha256: build.sha256.clone(),
            license_sha256: build.license_sha256.clone(),
            url,
            redirect_hosts: build.redirect_hosts.clone(),
        });
    }
    result
}

/// What went wrong with one download, in the words the panel shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Failure {
    /// No connection at all: the card is written without drivers.
    Offline,
    /// The server refused, cut the transfer, or redirected outside the catalog.
    Failed,
    /// The file differs from the catalog: never used.
    Changed,
}

pub struct DriverFetcher {
    client_for: fn(&DriverOffer, bool) -> reqwest::Result<reqwest::Client>,
    local_test: bool,
}

/// Only the catalog's own host and its listed redirect hosts, HTTPS on 443 without credentials.
pub fn allowed(url: &Url, offer: &DriverOffer, local_test: bool) -> bool {
    if local_test && url.scheme() == "http" && url.host_str() == Some("127.0.0.1") {
        return true; // Unit tests only: a fake server on this computer.
    }
    let origin = offer
        .url
        .as_ref()
        .and_then(|u| u.host_str().map(str::to_owned));
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port_or_known_default() == Some(443)
        && url.host_str().is_some_and(|h| {
            Some(h.to_owned()) == origin || offer.redirect_hosts.iter().any(|r| r == h)
        })
}

fn client(offer: &DriverOffer, local_test: bool) -> reqwest::Result<reqwest::Client> {
    let offer = offer.clone();
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .read_timeout(Duration::from_secs(60))
        .user_agent("astro-bridge-setup/1")
        .https_only(!local_test)
        .redirect(reqwest::redirect::Policy::custom(move |a| {
            if a.previous().len() >= 5 || !allowed(a.url(), &offer, local_test) {
                a.error("disallowed redirect")
            } else {
                a.follow()
            }
        }))
        .build()
}

impl Default for DriverFetcher {
    fn default() -> Self {
        Self {
            client_for: client,
            local_test: false,
        }
    }
}

impl DriverFetcher {
    #[cfg(test)]
    pub fn local_test() -> Self {
        Self {
            client_for: client,
            local_test: true,
        }
    }

    /// Download one offer into the cache, verified; an earlier verified copy is reused.
    pub async fn download<F: FnMut(u64, u64)>(
        &self,
        offer: &DriverOffer,
        cache_dir: &Path,
        mut progress: F,
        #[cfg(test)] test_url: Option<Url>,
    ) -> std::result::Result<PathBuf, Failure> {
        let (Some(size), Some(sha256)) = (offer.size, offer.sha256.as_deref()) else {
            return Err(Failure::Failed);
        };
        #[cfg(test)]
        let url = test_url.or_else(|| offer.url.clone());
        #[cfg(not(test))]
        let url = offer.url.clone();
        let Some(url) = url.filter(|u| allowed(u, offer, self.local_test)) else {
            return Err(Failure::Failed);
        };
        tokio::fs::create_dir_all(cache_dir)
            .await
            .map_err(|_| Failure::Failed)?;
        let destination = cache_dir.join(format!("{sha256}-{}.archive", offer.id));
        if verify_file(&destination, size, sha256).await {
            progress(size, size);
            return Ok(destination);
        }
        let client = (self.client_for)(offer, self.local_test).map_err(|_| Failure::Failed)?;
        let response = client.get(url).send().await.map_err(classify)?;
        if !response.status().is_success() {
            return Err(Failure::Failed);
        }
        if response.content_length().is_some_and(|n| n != size) {
            return Err(Failure::Changed);
        }
        let temp = tempfile::NamedTempFile::new_in(cache_dir).map_err(|_| Failure::Failed)?;
        let mut file = tokio::fs::File::from_std(temp.reopen().map_err(|_| Failure::Failed)?);
        let mut hash = Sha256::new();
        let mut received = 0u64;
        progress(0, size);
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(classify)?;
            received += chunk.len() as u64;
            if received > size {
                return Err(Failure::Changed);
            }
            hash.update(&chunk);
            file.write_all(&chunk).await.map_err(|_| Failure::Failed)?;
            progress(received, size);
        }
        file.flush().await.map_err(|_| Failure::Failed)?;
        file.sync_all().await.map_err(|_| Failure::Failed)?;
        drop(file);
        if received != size {
            return Err(Failure::Failed); // Cut short: try again.
        }
        if format!("{:x}", hash.finalize()) != sha256 {
            return Err(Failure::Changed);
        }
        temp.persist(&destination).map_err(|_| Failure::Failed)?;
        Ok(destination)
    }
}

fn classify(error: reqwest::Error) -> Failure {
    if error.is_connect() || (error.is_timeout() && !error.is_body()) {
        Failure::Offline
    } else {
        Failure::Failed
    }
}

/// True only for an existing regular file of exactly this size and SHA-256.
pub async fn verify_file(path: &Path, size: u64, sha256: &str) -> bool {
    let Ok(meta) = tokio::fs::symlink_metadata(path).await else {
        return false;
    };
    if !meta.file_type().is_file() || meta.len() != size {
        return false;
    }
    let Ok(bytes) = tokio::fs::read(path).await else {
        return false;
    };
    format!("{:x}", Sha256::digest(&bytes)) == sha256
}

/// The catalog file of a local test candidate (resources/candidate/drivers-catalog.json).
pub fn local_catalog(resource_dir: &Path) -> Result<Option<DriversCatalog>> {
    let path = resource_dir.join("candidate").join("drivers-catalog.json");
    match std::fs::symlink_metadata(&path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(Error::Io(e)),
        Ok(m) if !m.file_type().is_file() || m.len() as usize > MAX_CATALOG => {
            Err(invalid("invalid local driver catalog"))
        }
        Ok(_) => parse_catalog(&std::fs::read(path)?).map(Some),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    // A copy kept beside the crate: the public Setup tree does not contain the bridge application.
    const SHIPPED: &str = include_str!("../testdata/drivers-catalog.json");

    /// Where the bridge application is present, the copy must match what it ships.
    #[test]
    fn test_copy_matches_the_shipped_catalog() {
        let shipped = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../wifi_bridge/static/drivers-catalog.json");
        if let Ok(bytes) = std::fs::read(shipped) {
            assert_eq!(String::from_utf8_lossy(&bytes), SHIPPED);
        }
    }

    fn offer(url: &str, body: &[u8]) -> DriverOffer {
        DriverOffer {
            id: "testcam".into(),
            name: "Test camera".into(),
            verified_on_hardware: false,
            terms_url: "https://www.example.com/terms".into(),
            source: "download".into(),
            version: "1.0".into(),
            arch: "arm64".into(),
            size: Some(body.len() as u64),
            sha256: Some(format!("{:x}", Sha256::digest(body))),
            license_sha256: None,
            url: Some(Url::parse(url).unwrap()),
            redirect_hosts: vec!["cdn.example.com".into()],
        }
    }

    /// A one-shot local HTTP server: answers each connection with the next canned response.
    fn server(responses: Vec<Vec<u8>>) -> (String, std::thread::JoinHandle<()>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = format!("http://{}", listener.local_addr().unwrap());
        let handle = std::thread::spawn(move || {
            for response in responses {
                let (mut stream, _) = listener.accept().unwrap();
                let mut buffer = [0u8; 4096];
                let _ = stream.read(&mut buffer);
                let _ = stream.write_all(&response);
            }
        });
        (address, handle)
    }

    fn ok(body: &[u8]) -> Vec<u8> {
        let mut r = format!(
            "HTTP/1.1 200 OK\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
            body.len()
        )
        .into_bytes();
        r.extend_from_slice(body);
        r
    }

    fn run<T>(f: impl std::future::Future<Output = T>) -> T {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(f)
    }

    #[test]
    fn shipped_catalog_offers_only_installable_manufacturers() {
        let catalog = parse_catalog(SHIPPED.as_bytes()).unwrap();
        // No image carries a manufacturer library: ZWO is downloaded like every other manufacturer,
        // and a manufacturer whose fingerprints are not all pinned yet is not offered.
        let other = offers(&catalog, "arm64");
        assert!(other
            .iter()
            .all(|o| o.source == "download" && o.size.is_some()));
        let mut arm64 = other.iter().map(|o| o.id.as_str()).collect::<Vec<_>>();
        arm64.sort_unstable();
        assert_eq!(
            arm64,
            vec!["atik", "moravian", "player_one", "qhyccd", "zwo_asi"]
        );
        let zwo = other.iter().find(|o| o.id == "zwo_asi").unwrap();
        assert_eq!(
            zwo.url.as_ref().unwrap().host_str(),
            Some("dl.zwoastro.com")
        );
        let armv6 = offers(&catalog, "armv6");
        let mut armv6 = armv6.iter().map(|o| o.id.as_str()).collect::<Vec<_>>();
        armv6.sort_unstable();
        assert_eq!(armv6, vec!["player_one", "zwo_asi"]);
    }

    #[test]
    fn invalid_catalogs_are_refused() {
        let good: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let edits: Vec<Box<dyn Fn(&mut serde_json::Value)>> = vec![
            Box::new(|v| v["schema_version"] = 2.into()),
            Box::new(|v| v["drivers"][0]["terms_url"] = "http://www.zwoastro.com/".into()),
            Box::new(|v| {
                v["drivers"][0]["builds"][1]["url"] = "https://user@dl.zwoastro.com/x".into()
            }),
            Box::new(|v| v["drivers"][0]["builds"][1]["sha256"] = "ABC".into()),
            Box::new(|v| v["drivers"][0]["extra"] = 1.into()),
            Box::new(|v| v["drivers"][1]["id"] = "zwo_asi".into()),
            Box::new(|v| {
                v["drivers"][0]["builds"][1]["redirect_hosts"] = serde_json::json!(["bad host"])
            }),
        ];
        for edit in edits {
            let mut value = good.clone();
            edit(&mut value);
            assert!(parse_catalog(&serde_json::to_vec(&value).unwrap()).is_err());
        }
    }

    #[test]
    fn hosts_outside_the_catalog_and_insecure_transports_are_refused() {
        let o = offer("https://downloads.example.com/sdk.tar.gz", b"x");
        for s in [
            "https://downloads.example.com/a",
            "https://cdn.example.com/a",
        ] {
            assert!(allowed(&Url::parse(s).unwrap(), &o, false), "{s}");
        }
        for s in [
            "http://downloads.example.com/a",
            "https://downloads.example.com:8443/a",
            "https://user@downloads.example.com/a",
            "https://evil.example.net/a",
            "https://downloads.example.com.evil.test/a",
            "http://127.0.0.1/a",
        ] {
            assert!(!allowed(&Url::parse(s).unwrap(), &o, false), "{s}");
        }
    }

    #[test]
    fn verified_download_changed_file_cut_transfer_and_offline() {
        let body = b"synthetic manufacturer archive".to_vec();
        let cache = tempfile::tempdir().unwrap();
        let fetcher = DriverFetcher::local_test();
        let (address, handle) = server(vec![ok(&body)]);
        let o = offer("https://downloads.example.com/sdk.tar.gz", &body);
        let mut seen = Vec::new();
        let path = run(fetcher.download(
            &o,
            cache.path(),
            |r, t| seen.push((r, t)),
            Some(Url::parse(&format!("{address}/sdk")).unwrap()),
        ))
        .unwrap();
        handle.join().unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), body);
        assert_eq!(seen.last(), Some(&(body.len() as u64, body.len() as u64)));
        // A verified copy is reused without any request.
        assert!(run(fetcher.download(
            &o,
            cache.path(),
            |_, _| {},
            Some(Url::parse("http://127.0.0.1:9/x").unwrap())
        ))
        .is_ok());
        // The manufacturer changed the file at the same address.
        let other = tempfile::tempdir().unwrap();
        let mut changed = body.clone();
        changed[0] ^= 1;
        let (address, handle) = server(vec![ok(&changed)]);
        assert_eq!(
            run(fetcher.download(
                &o,
                other.path(),
                |_, _| {},
                Some(Url::parse(&format!("{address}/sdk")).unwrap())
            )),
            Err(Failure::Changed)
        );
        handle.join().unwrap();
        // Longer than the catalog says.
        let (address, handle) = server(vec![ok(&[body.clone(), b"more".to_vec()].concat())]);
        assert_eq!(
            run(fetcher.download(
                &o,
                other.path(),
                |_, _| {},
                Some(Url::parse(&format!("{address}/sdk")).unwrap())
            )),
            Err(Failure::Changed)
        );
        handle.join().unwrap();
        // Truncated transfer.
        let mut short = format!(
            "HTTP/1.1 200 OK\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
            body.len()
        )
        .into_bytes();
        short.extend_from_slice(&body[..5]);
        let (address, handle) = server(vec![short]);
        assert_eq!(
            run(fetcher.download(
                &o,
                other.path(),
                |_, _| {},
                Some(Url::parse(&format!("{address}/sdk")).unwrap())
            )),
            Err(Failure::Failed)
        );
        handle.join().unwrap();
        // Server error, then a redirect to a host outside the catalog.
        let (address, handle) = server(vec![b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\nconnection: close\r\n\r\n".to_vec(),
            b"HTTP/1.1 302 Found\r\nlocation: https://evil.example.net/x\r\ncontent-length: 0\r\nconnection: close\r\n\r\n".to_vec()]);
        let url = Url::parse(&format!("{address}/sdk")).unwrap();
        assert_eq!(
            run(fetcher.download(&o, other.path(), |_, _| {}, Some(url.clone()))),
            Err(Failure::Failed)
        );
        assert_eq!(
            run(fetcher.download(&o, other.path(), |_, _| {}, Some(url))),
            Err(Failure::Failed)
        );
        handle.join().unwrap();
        // Nothing listening: offline.
        let closed = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = closed.local_addr().unwrap().port();
        drop(closed);
        assert_eq!(
            run(fetcher.download(
                &o,
                other.path(),
                |_, _| {},
                Some(Url::parse(&format!("http://127.0.0.1:{port}/sdk")).unwrap())
            )),
            Err(Failure::Offline)
        );
        assert!(
            std::fs::read_dir(other.path()).unwrap().next().is_none(),
            "nothing kept after a failure"
        );
        // A production fetcher never accepts the local test server.
        assert_eq!(
            run(DriverFetcher::default().download(
                &o,
                other.path(),
                |_, _| {},
                Some(Url::parse("http://127.0.0.1:9/x").unwrap())
            )),
            Err(Failure::Failed)
        );
    }
}
