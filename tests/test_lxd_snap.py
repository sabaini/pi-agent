import os
import shutil
import subprocess
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPCRAFT_YAML = ROOT / "snap" / "snapcraft.yaml"
CONTAINER_IMAGE_CANDIDATES = ("ubuntu:26.04", "images:ubuntu/26.04")


def run_command(
    args: list[str],
    *,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            "command failed:\n"
            f"  {' '.join(args)}\n"
            f"  exit code: {result.returncode}\n"
            f"  stdout:\n{result.stdout}\n"
            f"  stderr:\n{result.stderr}"
        )
    return result


def run_lxc(
    container: str,
    command: str,
    *,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_command(["lxc", "exec", container, "--", "bash", "-lc", command], timeout=timeout, check=check)


def get_snapcraft_version() -> str:
    for line in SNAPCRAFT_YAML.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError("snap/snapcraft.yaml does not declare version")


def find_built_snap() -> Path | None:
    configured = os.environ.get("PI_AGENT_SNAP")
    if configured:
        return Path(configured).expanduser().resolve()

    candidates = sorted(
        ROOT.glob("pi-agent_*.snap"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return None


def require_lxd() -> None:
    if shutil.which("lxc") is None:
        raise unittest.SkipTest("lxc command is not installed")
    result = run_command(["lxc", "info"], timeout=30, check=False)
    if result.returncode != 0:
        raise unittest.SkipTest(f"lxd is not available: {result.stderr.strip() or result.stdout.strip()}")


def require_built_snap() -> Path:
    snap_path = find_built_snap()
    if snap_path is None:
        raise unittest.SkipTest("no built pi-agent_*.snap found; run `just pack` or set PI_AGENT_SNAP")
    if not snap_path.is_file():
        raise AssertionError(f"PI_AGENT_SNAP does not point to a file: {snap_path}")
    return snap_path


class LxdSnapIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        require_lxd()
        self.snap_path = require_built_snap()
        self.container = f"pi-agent-snap-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.addCleanup(self.delete_container)

    def delete_container(self) -> None:
        run_command(["lxc", "delete", "--force", self.container], timeout=120, check=False)

    def test_built_snap_runs_pi_version_in_ubuntu_26_04_container(self) -> None:
        self.launch_container()
        self.wait_for_container_exec()
        self.prepare_snapd()
        self.install_snap()
        self.ensure_pi_alias()

        result = run_lxc(self.container, "PI_OFFLINE=1 pi --version", timeout=60)
        version = get_snapcraft_version()
        self.assertIn(version, result.stdout.strip())

    def launch_container(self) -> None:
        errors: list[str] = []
        for image in CONTAINER_IMAGE_CANDIDATES:
            result = run_command(
                [
                    "lxc",
                    "launch",
                    image,
                    self.container,
                    "--quiet",
                    "-c",
                    "security.nesting=true",
                    "-c",
                    "security.syscalls.intercept.mknod=true",
                    "-c",
                    "security.syscalls.intercept.setxattr=true",
                ],
                timeout=300,
                check=False,
            )
            if result.returncode == 0:
                return
            errors.append(f"{image}: {result.stderr.strip() or result.stdout.strip()}")
            self.delete_container()

        raise AssertionError("failed to launch Ubuntu 26.04 LXD container:\n" + "\n".join(errors))

    def wait_for_container_exec(self) -> None:
        deadline = time.monotonic() + 180
        last_error = ""
        while time.monotonic() < deadline:
            result = run_command(["lxc", "exec", self.container, "--", "true"], timeout=10, check=False)
            if result.returncode == 0:
                return
            last_error = result.stderr.strip() or result.stdout.strip()
            time.sleep(2)
        raise AssertionError(f"container did not become executable: {last_error}")

    def prepare_snapd(self) -> None:
        run_lxc(
            self.container,
            "command -v cloud-init >/dev/null 2>&1 && cloud-init status --wait || true",
            timeout=300,
        )
        run_lxc(self.container, "apt-get update", timeout=300)
        run_lxc(
            self.container,
            "DEBIAN_FRONTEND=noninteractive apt-get install -y snapd squashfuse",
            timeout=300,
        )
        run_lxc(self.container, "systemctl enable --now snapd.socket snapd.service", timeout=120)
        run_lxc(self.container, "snap wait system seed.loaded", timeout=300)

    def install_snap(self) -> None:
        run_command(
            ["lxc", "file", "push", str(self.snap_path), f"{self.container}/tmp/pi-agent.snap"],
            timeout=120,
        )
        run_lxc(self.container, "snap install --classic --dangerous /tmp/pi-agent.snap", timeout=600)

    def ensure_pi_alias(self) -> None:
        # Locally-installed dangerous snaps do not always get store-managed aliases.
        # The snap declares the pi alias, but enable it manually in this isolated
        # container so the runtime check exercises the user-facing command name.
        result = run_lxc(self.container, "command -v pi >/dev/null 2>&1", timeout=30, check=False)
        if result.returncode == 0:
            return
        run_lxc(self.container, "snap alias pi-agent.pi pi", timeout=60)
