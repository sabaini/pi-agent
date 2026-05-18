# pi-agent snap

Snap packaging for the Pi terminal coding agent.

It packages the published Linux binary from `earendil-works/pi-mono` GitHub releases and uses classic confinement so Pi can edit arbitrary project files and run the host toolchain.

## Build

```bash
just pack
```

For a local host build without LXD isolation:

```bash
just pack-destructive
```

Install the resulting snap locally:

```bash
sudo snap install ./pi-agent_*.snap --classic --dangerous
sudo snap alias pi-agent.pi pi  # needed if the local install does not auto-enable the alias
pi --version
```

## Update the packaged Pi version

The snap version is adopted from the Pi release selected during the build. For a
reproducible build, set `PI_AGENT_VERSION` explicitly:

```bash
PI_AGENT_VERSION=0.75.3 just pack
```

If `PI_AGENT_VERSION` is not set, the build uses `PI_AGENT_RELEASE_TAG` when
present, otherwise it resolves the latest release from GitHub. The selected
release downloads:

```text
https://github.com/earendil-works/pi-mono/releases/download/v<VERSION>/pi-linux-<x64|arm64>.tar.gz
```

Supported Snap architectures are `amd64` and `arm64`.

During packaging, the fetch script verifies the downloaded archive against the
SHA-256 digest published for that release asset by GitHub.

## Test

```bash
just test
```

The unit tests validate the Snapcraft metadata, release URL mapping, and wrapper behavior without downloading a release or invoking Snapcraft. To run only those tests:

```bash
just test-unit
```

If LXD is available and a built snap exists, the test suite also creates an Ubuntu 26.04 container, installs the snap, and runs `pi --version` without contacting a model. To run only that integration test:

```bash
just test-lxd
```

Set `PI_AGENT_SNAP=/path/to/pi-agent.snap` to test a specific snap. If no snap is configured, the test uses the newest `pi-agent_*.snap` in the repository root. Use `just pack-test-lxd` to build with Snapcraft and then run the LXD integration test.

## Notes

- The snap name is `pi-agent`, but the packaged app command and wrapper are `pi`.
- The snap declares the `pi` alias. The Snap Store may still require alias approval; without it, snapd exposes the app as `pi-agent.pi`.
- Pi keeps its normal configuration directory, `~/.pi`, for compatibility with other installs.
- The wrapper keeps the caller's `PATH` first, then appends snap-staged helper binaries such as `rg` and `fdfind`.
- Self-update should be handled by `snap refresh pi-agent`.
