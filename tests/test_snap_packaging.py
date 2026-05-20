import hashlib
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPCRAFT_YAML = ROOT / "snap" / "snapcraft.yaml"
FETCH_RELEASE = ROOT / "snap" / "local" / "fetch-release.sh"
WRAPPER = ROOT / "snap" / "local" / "pi"
JUSTFILE = ROOT / "justfile"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_fetch_dry_run(arch: str, version: str = "0.74.0") -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "CRAFT_ARCH_BUILD_FOR": arch,
            "CRAFT_PART_INSTALL": "/tmp/pi-agent-test-install",
            "PI_AGENT_DRY_RUN": "1",
            "PI_AGENT_VERSION": version,
        }
    )
    result = subprocess.run(
        [str(FETCH_RELEASE)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.strip().splitlines())


def make_release_archive(tmp: Path) -> Path:
    payload = tmp / "payload"
    binary = payload / "pi" / "pi"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nprintf 'fake pi\\n'\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    archive = tmp / "pi-linux-x64.tar.gz"
    subprocess.run(["tar", "-czf", str(archive), "-C", str(payload), "pi"], check=True)
    return archive


def write_fake_curl(fakebin: Path) -> None:
    fakebin.mkdir()
    curl = fakebin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "output=\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --output)\n"
        "      shift\n"
        "      output=\"$1\"\n"
        "      ;;\n"
        "  esac\n"
        "  shift || break\n"
        "done\n"
        "if [ -n \"$output\" ]; then\n"
        "  cp \"$TEST_RELEASE_ARCHIVE\" \"$output\"\n"
        "else\n"
        "  printf '%s\\n' \"$TEST_RELEASE_JSON\"\n"
        "fi\n",
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)


def run_fetch_with_fake_release(
    archive: Path,
    install: Path,
    fakebin: Path,
    published_digest: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "CRAFT_ARCH_BUILD_FOR": "amd64",
            "CRAFT_PART_INSTALL": str(install),
            "PATH": f"{fakebin}{os.pathsep}{env['PATH']}",
            "PI_AGENT_VERSION": "0.74.0",
            "PI_AGENT_RELEASE_API_URL": "https://api.example.invalid/repos/pi/releases/tags/v0.74.0",
            "TEST_RELEASE_ARCHIVE": str(archive),
            "TEST_RELEASE_JSON": (
                '{"assets":[{"url":"https://api.example.invalid/assets/1",'
                '"name":"pi-linux-x64.tar.gz",'
                f'"digest":"sha256:{published_digest}"'
                '}]}'
            ),
        }
    )
    return subprocess.run(
        [str(FETCH_RELEASE)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class SnapcraftYamlTests(unittest.TestCase):
    def test_declares_expected_snap_metadata(self) -> None:
        text = read_text(SNAPCRAFT_YAML)
        self.assertRegex(text, r"(?m)^name: pi-agent$")
        self.assertRegex(text, r"(?m)^base: core24$")
        self.assertRegex(text, r"(?m)^adopt-info: pi$")
        self.assertNotRegex(text, r"(?m)^version:")
        self.assertRegex(text, r"(?m)^confinement: classic$")
        self.assertRegex(text, r"(?m)^license: MIT$")
        self.assertRegex(text, r"(?m)^contact: https://github.com/earendil-works/pi/issues$")
        self.assertRegex(text, r"(?m)^grade: devel$")

    def test_declares_supported_architectures(self) -> None:
        text = read_text(SNAPCRAFT_YAML)
        self.assertRegex(text, r"(?ms)^platforms:\n  amd64:\n")
        self.assertNotRegex(text, r"(?m)^  arm64:")

    def test_declares_pi_app_command(self) -> None:
        text = read_text(SNAPCRAFT_YAML)
        self.assertRegex(text, r"(?ms)^apps:\n  pi:\n    command: bin/pi\n    aliases:\n      - pi")

    def test_uses_published_release_fetcher_and_wrapper(self) -> None:
        text = read_text(SNAPCRAFT_YAML)
        self.assertIn('craftctl set version="$version"', text)
        self.assertIn('PI_AGENT_VERSION="$version" "$CRAFT_PROJECT_DIR/snap/local/fetch-release.sh"', text)
        self.assertIn('snap/local/pi', text)

    def test_patchelf_handles_helpers_and_pi_binary(self) -> None:
        text = read_text(SNAPCRAFT_YAML)
        self.assertRegex(
            text,
            r"(?ms)^  staged-tools:\n"
            r"    plugin: nil\n"
            r"(?:    #.*\n)*"
            r"    build-attributes:\n"
            r"      - enable-patchelf\n"
            r"    stage-packages:\n"
            r"      - ca-certificates\n"
            r"      - fd-find\n"
            r"      - git\n"
            r"      - ripgrep",
        )
        pi_part = re.search(r"(?ms)^parts:\n.*?^  pi:\n(.*?)(?:\n  [A-Za-z0-9_-]+:|\Z)", text)
        self.assertIsNotNone(pi_part)
        assert pi_part is not None
        self.assertNotIn("enable-patchelf", pi_part.group(1))
        self.assertIn("      - patchelf\n", pi_part.group(1))
        self.assertIn(
            "patchelf --force-rpath --set-rpath /snap/core24/current/lib/x86_64-linux-gnu",
            pi_part.group(1),
        )
        self.assertIn(
            "patchelf --set-interpreter /snap/core24/current/lib64/ld-linux-x86-64.so.2",
            pi_part.group(1),
        )


class ScriptTests(unittest.TestCase):
    def test_scripts_are_executable(self) -> None:
        self.assertTrue(os.access(FETCH_RELEASE, os.X_OK), f"{FETCH_RELEASE} is not executable")
        self.assertTrue(os.access(WRAPPER, os.X_OK), f"{WRAPPER} is not executable")

    def test_fetch_release_maps_amd64_to_x64_asset(self) -> None:
        values = run_fetch_dry_run("amd64")
        self.assertEqual(values["arch"], "x64")
        self.assertEqual(values["asset"], "pi-linux-x64.tar.gz")
        self.assertEqual(
            values["url"],
            "https://github.com/earendil-works/pi-mono/releases/download/v0.74.0/pi-linux-x64.tar.gz",
        )

    def test_fetch_release_rejects_arm64_until_tested(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "CRAFT_ARCH_BUILD_FOR": "arm64",
                "CRAFT_PART_INSTALL": "/tmp/pi-agent-test-install",
                "PI_AGENT_DRY_RUN": "1",
                "PI_AGENT_VERSION": "0.74.0",
            }
        )
        result = subprocess.run(
            [str(FETCH_RELEASE)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported snap architecture: arm64", result.stderr)

    def test_fetch_release_rejects_unsupported_architecture(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "CRAFT_ARCH_BUILD_FOR": "riscv64",
                "CRAFT_PART_INSTALL": "/tmp/pi-agent-test-install",
                "PI_AGENT_DRY_RUN": "1",
                "PI_AGENT_VERSION": "0.74.0",
            }
        )
        result = subprocess.run(
            [str(FETCH_RELEASE)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported snap architecture: riscv64", result.stderr)

    def test_fetch_release_accepts_github_published_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            archive = make_release_archive(tmp)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            install = tmp / "install"
            fakebin = tmp / "fakebin"
            write_fake_curl(fakebin)

            result = run_fetch_with_fake_release(archive, install, fakebin, digest)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((install / "pi" / "pi").exists())

    def test_fetch_release_rejects_mismatched_github_published_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            archive = make_release_archive(tmp)
            install = tmp / "install"
            fakebin = tmp / "fakebin"
            write_fake_curl(fakebin)

            result = run_fetch_with_fake_release(archive, install, fakebin, "0" * 64)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAILED", result.stdout + result.stderr)
            self.assertFalse((install / "pi" / "pi").exists())

    def test_wrapper_requires_snap_runtime(self) -> None:
        env = os.environ.copy()
        env.pop("SNAP", None)
        result = subprocess.run(
            [str(WRAPPER)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SNAP is not set", result.stderr)

    def test_wrapper_execs_bundled_pi_and_appends_snap_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp)
            pi_dir = snap / "pi"
            pi_dir.mkdir()
            (snap / "usr" / "bin").mkdir(parents=True)
            (snap / "bin").mkdir()
            fake_pi = pi_dir / "pi"
            fake_pi.write_text(
                "#!/bin/sh\n"
                "printf 'argv=%s\\n' \"$*\"\n"
                "printf 'path=%s\\n' \"$PATH\"\n",
                encoding="utf-8",
            )
            fake_pi.chmod(fake_pi.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["SNAP"] = str(snap)
            env["PATH"] = "/host/bin:/usr/bin"
            result = subprocess.run(
                [str(WRAPPER), "one", "two"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

        values = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
        self.assertEqual(values["argv"], "one two")
        path_entries = values["path"].split(":")
        self.assertEqual(path_entries[:2], ["/host/bin", "/usr/bin"])
        self.assertIn(str(snap / "usr" / "bin"), path_entries)
        self.assertIn(str(snap / "bin"), path_entries)


class JustfileTests(unittest.TestCase):
    def test_expected_targets_exist(self) -> None:
        text = read_text(JUSTFILE)
        for target in ["test", "check", "pack", "pack-destructive", "clean"]:
            self.assertRegex(text, rf"(?m)^{re.escape(target)}:")


if __name__ == "__main__":
    unittest.main()
