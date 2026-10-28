# Contributing an implementation

Use English for source, comments, documentation, issues and pull requests. Do not publish personal names, private conversations, credentials, actual device identifiers or unreviewed captures.

1. Read the [platform contract](doc/platform-contract.md).
2. Pin a tested Core wheel/source and checksums.
3. Provide plan-first installation and start/stop/log/backup/update/recovery procedures.
4. Test helpers and isolated networking before physical hardware.
5. Report exact host/adapter/model/app evidence and remaining limitations.
6. Preserve original/third-party licenses and required corresponding source when redistributing binaries.

Application/UI/protocol changes and declarative device plugins belong in [Core](https://github.com/Sqound/astro-bridge-core). Do not copy the application into a second tracked tree.

```sh
python3 tools/core-artifact.py fetch
python3 tools/test-platforms.py --wheel build/core/astro_bridge_core-1.0.1-py3-none-any.whl
python3 tools/verify-staging.py --wheel build/core/astro_bridge_core-1.0.1-py3-none-any.whl
```

Contributions to the original project are made under the [MIT License](LICENSE).
