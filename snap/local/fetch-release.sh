#!/bin/sh
set -eu

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

[ -n "${CRAFT_PART_INSTALL:-}" ] || die 'CRAFT_PART_INSTALL is not set'
[ -n "${CRAFT_ARCH_BUILD_FOR:-}" ] || die 'CRAFT_ARCH_BUILD_FOR is not set'

version="${PI_AGENT_VERSION:-${SNAPCRAFT_PROJECT_VERSION:-}}"
[ -n "$version" ] || die 'PI_AGENT_VERSION or SNAPCRAFT_PROJECT_VERSION must be set'
version="${version#v}"

case "$CRAFT_ARCH_BUILD_FOR" in
	amd64 | x86_64)
		asset_arch='x64'
		;;
	arm64 | aarch64)
		asset_arch='arm64'
		;;
	*)
		die "unsupported snap architecture: $CRAFT_ARCH_BUILD_FOR"
		;;
esac

release_repo="${PI_AGENT_RELEASE_REPO:-earendil-works/pi-mono}"
release_tag="${PI_AGENT_RELEASE_TAG:-v$version}"
release_base_url="${PI_AGENT_RELEASE_BASE_URL:-https://github.com/$release_repo/releases/download}"
asset="pi-linux-$asset_arch.tar.gz"
url="${PI_AGENT_RELEASE_URL:-$release_base_url/$release_tag/$asset}"

if [ "${PI_AGENT_DRY_RUN:-}" = '1' ]; then
	printf 'version=%s\n' "$version"
	printf 'arch=%s\n' "$asset_arch"
	printf 'asset=%s\n' "$asset"
	printf 'url=%s\n' "$url"
	exit 0
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM
archive="$tmpdir/$asset"

curl --fail --location --show-error --retry 5 --retry-delay 2 --output "$archive" "$url"

if [ -n "${PI_AGENT_RELEASE_SHA256:-}" ]; then
	(
		cd "$tmpdir"
		printf '%s  %s\n' "$PI_AGENT_RELEASE_SHA256" "$asset" | sha256sum --check --strict -
	)
fi

mkdir -p "$CRAFT_PART_INSTALL"
tar -xzf "$archive" -C "$CRAFT_PART_INSTALL"

binary="$CRAFT_PART_INSTALL/pi/pi"
[ -f "$binary" ] || die "release archive did not contain pi/pi from $url"
chmod +x "$binary"
