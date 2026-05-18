#!/bin/sh
set -eu

die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}

normalize_sha256() {
	digest="${1#sha256:}"
	case "$digest" in
		'' | *[!0123456789abcdefABCDEF]*)
			return 1
			;;
	esac
	[ "${#digest}" -eq 64 ] || return 1
	printf '%s\n' "$digest" | tr 'ABCDEF' 'abcdef'
}

fetch_published_sha256() {
	api_url="$1"
	asset="$2"

	curl --fail --location --show-error --retry 5 --retry-delay 2 \
		--header 'Accept: application/vnd.github+json' \
		--header 'X-GitHub-Api-Version: 2022-11-28' \
		"$api_url" |
		tr -d '\n\r' |
		sed \
			-e 's/"name"[[:space:]]*:[[:space:]]*"/"name":"/g' \
			-e 's/"digest"[[:space:]]*:[[:space:]]*"sha256:/"digest":"sha256:/g' |
		awk -v asset="$asset" '
			{
				needle = "\"name\":\"" asset "\""
				pos = index($0, needle)
				if (pos == 0) next

				rest = substr($0, pos)
				next_name_pos = index(substr(rest, length(needle) + 1), "\"name\":\"")
				marker = "\"digest\":\"sha256:"
				digest_pos = index(rest, marker)
				if (digest_pos == 0) next
				if (next_name_pos > 0 && digest_pos > length(needle) + next_name_pos) next

				digest_start = digest_pos + length(marker)
				digest = substr(rest, digest_start, 64)
				digest_end = substr(rest, digest_start + 64, 1)
				if (digest_end == "\"" && digest ~ /^[0-9a-fA-F]+$/ && length(digest) == 64) {
					print tolower(digest)
					found = 1
					exit
				}
			}
			END { if (!found) exit 1 }
		'
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
release_api_url="${PI_AGENT_RELEASE_API_URL:-https://api.github.com/repos/$release_repo/releases/tags/$release_tag}"
asset="pi-linux-$asset_arch.tar.gz"
url="${PI_AGENT_RELEASE_URL:-$release_base_url/$release_tag/$asset}"

if [ "${PI_AGENT_DRY_RUN:-}" = '1' ]; then
	printf 'version=%s\n' "$version"
	printf 'arch=%s\n' "$asset_arch"
	printf 'asset=%s\n' "$asset"
	printf 'url=%s\n' "$url"
	exit 0
fi

release_sha256="${PI_AGENT_RELEASE_SHA256:-}"
if [ -n "$release_sha256" ]; then
	if ! release_sha256="$(normalize_sha256 "$release_sha256")"; then
		die 'PI_AGENT_RELEASE_SHA256 must be a 64-character sha256 digest'
	fi
elif [ "${PI_AGENT_SKIP_RELEASE_SHA256:-}" != '1' ]; then
	if ! release_sha256="$(fetch_published_sha256 "$release_api_url" "$asset")"; then
		die "could not find sha256 digest for $asset in GitHub release metadata at $release_api_url"
	fi
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM
archive="$tmpdir/$asset"

curl --fail --location --show-error --retry 5 --retry-delay 2 --output "$archive" "$url"

if [ -n "$release_sha256" ]; then
	(
		cd "$tmpdir"
		printf '%s  %s\n' "$release_sha256" "$asset" | sha256sum --check --strict -
	)
fi

mkdir -p "$CRAFT_PART_INSTALL"
tar -xzf "$archive" -C "$CRAFT_PART_INSTALL"

binary="$CRAFT_PART_INSTALL/pi/pi"
[ -f "$binary" ] || die "release archive did not contain pi/pi from $url"
chmod +x "$binary"
