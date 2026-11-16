# Private candidate preparation

A candidate is unpublished. Version metadata alone never identifies its bytes.
The ordinary `core.lock.json` remains the published pin. A local
`core.candidate.json` records the candidate hashes with `unpublished: true` and
has no download URLs. Never upload a candidate over an existing release asset.

For offline verification, run from the implementations candidate root, using
absolute paths to the matching local wheel and a new staging directory:

```sh
python3 tools/core-artifact.py --candidate-lock core.candidate.json verify "$WHEEL"
python3 tools/core-artifact.py --candidate-lock core.candidate.json stage "$WHEEL" "$STAGE"
python3 tools/test-platforms.py --candidate-lock core.candidate.json --wheel "$WHEEL"
python3 tools/verify-staging.py --candidate-lock core.candidate.json --wheel "$WHEEL"
```

The acceptance documents' `tools/`, `scripts/`, `wifi_bridge/` and `cameras/`
commands run **from `$STAGE`**, the project created by the stage command above.
It combines only the verified wheel and reviewed platform sources. Staging does
not install software, invoke Docker, access hardware or download a vendor SDK.
Keep evidence and application packages in new private directories outside it.

The maker-key fetch **code** is in Core (`wifi_bridge/maker_keys/fetch.py`), with
its generic service and API. Actual keys, manufacturer packages, signed URLs,
vendor binaries and owner evidence are excluded from both source exports.
USB/udev setup, SDK installation and optional INDI host prerequisites belong to
implementations. Core retains the optional Linux reference backend and services
contract; unsupported or absent host capabilities stay unavailable.

Portable checks and app-only package verification do not establish official
ConformU acceptance or physical radio/camera behavior. No image build, Docker
build, SDK execution, deployment, OTA installation or publication is authorized
by these preparation commands. Hardware acceptance remains documented, not
verified on hardware.
