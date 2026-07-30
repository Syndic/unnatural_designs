# syntax=docker/dockerfile:1
#
# Shared devcontainer base image: the container-side host-plumbing that Syndic repos would
# otherwise hand-port between each other. See README.md in this directory.
#
# Deliberately RUN-free. With no RUN there is nothing to emulate, so building for
# linux/amd64 + linux/arm64 needs no QEMU — which matters because developer hosts are Apple
# Silicon and CI runners are amd64. Adding a RUN layer here makes multi-arch builds slow;
# think hard before doing it.
#
# Consumers keep their own `features` block. The devcontainer CLI applies features on top of
# whatever the image already contains and does not dedupe against it, so baking them here
# would either waste build time or pull each consumer's feature pins (and its
# devcontainer-lock.json) out of the repo that consumes them.
FROM mcr.microsoft.com/devcontainers/base:debian

LABEL org.opencontainers.image.source="https://github.com/Syndic/unnatural_designs"
LABEL org.opencontainers.image.description="Shared devcontainer git/host plumbing for Syndic repos"

# The dispatcher goes straight onto PATH and finds its library by absolute fallback path, so
# no symlink — and therefore no RUN — is needed. It is installed without the .sh suffix
# because it is a command; the sources keep the suffix so shellcheck's selector picks them up.
COPY --chmod=0755 scripts/devcontainer-plumbing.sh /usr/local/bin/devcontainer-plumbing
COPY --chmod=0644 scripts/lib.sh /usr/local/share/devcontainer-plumbing/lib.sh
