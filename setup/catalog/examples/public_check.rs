use astro_bridge_setup_catalog::{Catalog, Target};
use std::path::Path;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cache = std::env::args()
        .nth(1)
        .ok_or("provide an SSD cache directory")?;
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?
        .block_on(async {
            let catalog = Catalog::new()?;
            let artifact = catalog
                .resolve(Target::Docker)
                .await?
                .ok_or("no Docker release")?;
            println!(
                "{} {} {} {}",
                artifact.tag, artifact.asset, artifact.size, artifact.sha256
            );
            let path = catalog
                .download(&artifact, Path::new(&cache), |_| {})
                .await?;
            println!("verified {}", path.display());
            Ok::<(), Box<dyn std::error::Error>>(())
        })
}
