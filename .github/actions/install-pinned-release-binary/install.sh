#!/usr/bin/env bash
# Installs the release of $TOOL that .devcontainer/Dockerfile pins; see action.yml.
set -euo pipefail

machine="$(uname -m)"
case "${machine}" in
  x86_64) pin_arch=AMD64 deb_arch=amd64 ;;
  aarch64) pin_arch=ARM64 deb_arch=arm64 ;;
  *)
    echo "no pinned digest for ${machine}" >&2
    exit 1
    ;;
esac

# Each tool's asset layout, as its Dockerfile RUN downloads it. `{version}` is the pinned tag and
# `{number}` the tag without its `v`. Matched before the Dockerfile is read, so a misspelled tool
# reports itself rather than a missing ARG.
case "${TOOL}" in
  shellcheck)
    url="https://github.com/koalaman/shellcheck/releases/download/{version}/shellcheck-{version}.linux.${machine}.tar.xz"
    member="shellcheck-{version}/shellcheck"
    ;;
  actionlint)
    url="https://github.com/rhysd/actionlint/releases/download/{version}/actionlint_{number}_linux_${deb_arch}.tar.gz"
    member="actionlint"
    ;;
  *)
    echo "no release layout known for ${TOOL}" >&2
    exit 1
    ;;
esac

prefix="${TOOL^^}"
version="$(sed -n "s/^ARG ${prefix}_VERSION=//p" .devcontainer/Dockerfile)"
sha512="$(sed -n "s/^ARG ${prefix}_SHA512_${pin_arch}=//p" .devcontainer/Dockerfile)"
# An ARG that moved or got renamed must fail here, not silently fall through to whatever the
# runner image happens to ship.
if [ -z "${version}" ] || [ -z "${sha512}" ]; then
  echo "no ${prefix}_VERSION/${prefix}_SHA512_${pin_arch} in .devcontainer/Dockerfile" >&2
  exit 1
fi
expand() {
  local s="${1//\{version\}/${version}}"
  printf '%s' "${s//\{number\}/${version#v}}"
}
url="$(expand "${url}")"
member="$(expand "${member}")"

workdir="$(mktemp -d)"
curl -fsSL -o "${workdir}/archive" "${url}"
echo "${sha512}  ${workdir}/archive" | sha512sum -c -
tar -xf "${workdir}/archive" -C "${workdir}" "${member}"
sudo install -m 0755 "${workdir}/${member}" "/usr/local/bin/${TOOL}"
# The runner image ships its own copies of some of these, and /usr/local/bin winning on PATH is an
# assumption about that image — a shadowed binary has to fail here rather than lint green under
# some other version.
resolved="$(command -v "${TOOL}")"
if [ "${resolved}" != "/usr/local/bin/${TOOL}" ]; then
  echo "${TOOL} on PATH resolves to ${resolved}, not the pinned /usr/local/bin/${TOOL}" >&2
  exit 1
fi
"${TOOL}" --version
