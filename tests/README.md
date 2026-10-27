# Implementation tests

Build the locked core wheel, stage it with the recipes, then run:

```sh
python3 tools/test-platforms.py --wheel /path/to/astro_bridge_core-1.0.0-py3-none-any.whl
```

The runner creates a temporary staged project and exercises Raspberry overlay inspection, persistence helpers, TLS/HTTPS (with temporary certificates only), credential helpers, flasher guards and OTA validation/rollback against that exact core. It never starts a physical telescope. On macOS use OpenSSL 3 rather than OpenSSL 4 for the established certificate fixtures. No shared private checkout is required.

Docker smoke tests and privileged namespace tests are explicit separate commands in the release validation document. Their pass/fail must not be substituted for physical Wi-Fi/phone evidence.
