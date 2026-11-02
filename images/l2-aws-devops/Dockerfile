# L2 for an AWS infrastructure repository that uses Atmos and Terraform:
# the shared L2 image (pre-commit, the language runtimes, the baked linters)
# with the tools such a repository's hooks, tests and commands call by name.
# It runs like any other L2 image, with no network and no credentials; a
# repository names it in its own .devcontainer/l2/Dockerfile, pinned by
# digest, and host/workbench init writes that file for a repository with an
# atmos.yaml or Terraform files. docs/IMAGES.md says who it is for and
# docs/TOOL_SOURCES.md where each tool comes from.
#
# Every tool but tflint is pinned to match a consuming infrastructure
# repository, so a hook or a command behaves here exactly as it does there.
# Those pins are bumped deliberately, in that repository first, and
# .github/renovate.json5 keeps Renovate from proposing them until then; they
# stay in the annotated shapes Renovate and Pin Only read, so turning that
# rule off is all it takes to let them move again.
ARG L2_IMAGE=localhost/airlock-l2:local

# The tools pinned by a version argument rather than an image tag, declared
# here, before any FROM, so the stage that installs each one and the version
# check in the image read the one value. helm, eksctl and the AWS CLI come
# from release archives (the releases stage below), terraform from
# HashiCorp's apt repository at that exact version.
# renovate: datasource=github-releases depName=helm/helm
ARG HELM_VERSION=v4.1.1
# renovate: datasource=github-releases depName=hashicorp/terraform
ARG TERRAFORM_VERSION=v1.14.9
# renovate: datasource=github-releases depName=eksctl-io/eksctl
ARG EKSCTL_VERSION=v0.231.0
# renovate: datasource=github-tags depName=aws/aws-cli
ARG AWSCLI_VERSION=2.32.6

# The tools that publish an official container image, copied out of it, the
# same way the shared L2 image takes hadolint and actionlint. Each one is a
# statically linked Go binary, so it runs on Ubuntu exactly as it does in its
# own image. Pinned by tag and digest, and the version check at the end of
# this file reads the tag from these lines.
FROM ghcr.io/cloudposse/atmos:1.229.0@sha256:9777fffcaf0ac3f0ef96ef584b9d7a4f8b9ac3640950149d525d30c9874a556d AS atmos
FROM ghcr.io/terraform-linters/tflint:v0.65.0@sha256:2788f4d4fc663c0e953b011b01bf443fe5bfce7587ee916e588e6057b85d6359 AS tflint
FROM registry.k8s.io/kubectl:v1.35.2@sha256:91d5a6b5c45cf4e0f95de3dda6a957725b5f4daa0193c63074aab7f4bd85eaee AS kubectl
FROM ghcr.io/helmfile/helmfile:v1.4.4@sha256:d6dd15bfc569b9b02a2cfb995ef48927a21d367a51f8b95d58bcce820268ba31 AS helmfile
FROM docker.io/bitnami/sealed-secrets-kubeseal:0.39.1@sha256:df9497ee337b7984010c92d061cef038bacf07a2938b58e7293720ed69c7ce09 AS kubeseal
FROM us-docker.pkg.dev/fairwinds-ops/oss/pluto:v5.22.8@sha256:0254fe08b2eef6661bbd62f257520f6cccba4063262b4fe9b6e68731731302c2 AS pluto
FROM docker.io/mikefarah/yq:4.52.4@sha256:a4c2cafdf4674b2dc3c6ac457fe1486d55d7d93edb3ed4459637afda3c286af6 AS yq

# The tools taken from a release archive, each checked against what its own
# project signs before anything is unpacked. A throwaway stage, so cosign and
# the downloads stay out of the image; only what was verified is copied out.
# hadolint ignore=DL3006
FROM ${L2_IMAGE} AS releases
# A build stage only; nothing from it runs in L2.
# hadolint ignore=DL3002
USER 0:0
WORKDIR /tmp/releases
ENV HOME=/tmp/releases/home

# helm: Helm publishes no image of its own and its apt repository is signed
# by its hosting provider rather than by the project, so the release archive
# is the one path where the project vouches for the bytes. The archive is
# signed with GPG by a release manager, whose key is one of those in Helm's
# KEYS file, vendored under keyrings/helm.asc (the armored blocks of
# https://github.com/helm/helm/blob/main/KEYS). That file's primary
# certificates are checked against the reviewed set below before gpg reads
# it, exactly as the base image checks its apt keys, so a key added upstream
# or a tampered file is a reviewed change rather than a new signer.
COPY keyrings/helm.asc /tmp/releases/helm.asc
ARG HELM_VERSION
# hadolint ignore=DL4006
RUN set -eu; \
  mkdir -p "${HOME}"; \
  want="208DD36ED5BB3745A16743A4C7C6FBB5B91C1155,49D09C86C3DC8DA3F0A076221EF612347F8A9958,4AB45F1CB0D292975C6371436E2A23D806B6E6DD,672C657BE06B4B30969C4A57461449C25E36B98E,76939899B137D575D3274E756DCCB9D752D35BA8,7FEC81FACC7FFB2A010ADD13C2D40F4D8196E874,967F8AC5E2216F9F4FD270AD92AA783CBAAE8E3B,ABA2529598F6626C420D335B62F49E747D911B60,BF888333D96A1C18E2682AAED79D67C9EC016739,F1261BDE929012C8FF2E501D6EA5D7598529A53E"; \
  got="$(gpg --show-keys --with-colons helm.asc \
    | awk -F: '$1=="pub"{p=1;next} $1=="fpr"&&p{print $10;p=0;next} {p=0}' \
    | sort | paste -sd, -)"; \
  if [ "${got}" != "${want}" ]; then \
    echo "helm.asc: primary certificates ${got:-none} do not match the reviewed set ${want}" >&2; \
    exit 1; \
  fi; \
  gpg --dearmor < helm.asc > helm.gpg; \
  archive="helm-${HELM_VERSION}-linux-amd64.tar.gz"; \
  curl --proto '=https' --tlsv1.2 -fsSL -o "${archive}" "https://get.helm.sh/${archive}"; \
  curl --proto '=https' --tlsv1.2 -fsSL -o "${archive}.asc" \
    "https://github.com/helm/helm/releases/download/${HELM_VERSION}/${archive}.asc"; \
  gpgv --keyring ./helm.gpg "${archive}.asc" "${archive}"; \
  mkdir -p /out helm; \
  tar -xzf "${archive}" -C helm linux-amd64/helm; \
  install -m 0755 helm/linux-amd64/helm /out/helm

# eksctl: its image on ECR Public stops at v0.225.0 and is not the release
# build (measured 2026-11-02: v0.221.0 there reports 0.221.0-dev and differs
# from the archive's binary), so it comes from the release. v0.231.0 is the
# first release eksctl signs: its checksum file is signed with cosign
# keyless, and the identity is pinned to eksctl's own release workflow at
# this very tag, so a valid signature from any other workflow, repository or
# tag is refused. The archive is then checked against that file. cosign
# comes from Ubuntu and stays in this stage.
ARG EKSCTL_VERSION
# hadolint ignore=DL3008,DL4006
RUN set -eu; \
  apt-get update; \
  apt-get install -y --no-install-recommends cosign; \
  rm -rf /var/lib/apt/lists/*; \
  mkdir -p "${HOME}"; \
  base="https://github.com/eksctl-io/eksctl/releases/download/${EKSCTL_VERSION}"; \
  for f in eksctl_checksums.txt eksctl_checksums.txt.sigstore.json eksctl_Linux_amd64.tar.gz; do \
    curl --proto '=https' --tlsv1.2 -fsSL -o "${f}" "${base}/${f}"; \
  done; \
  version="$(printf '%s' "${EKSCTL_VERSION#v}" | sed 's/\./\\./g')"; \
  cosign verify-blob --new-bundle-format \
    --bundle eksctl_checksums.txt.sigstore.json \
    --certificate-identity-regexp "^https://github\.com/eksctl-io/eksctl/\.github/workflows/publish-release\.yaml@refs/tags/v?${version}\$" \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    eksctl_checksums.txt; \
  grep ' eksctl_Linux_amd64\.tar\.gz$' eksctl_checksums.txt | sha256sum --check --strict -; \
  mkdir -p /out eksctl; \
  tar -xzf eksctl_Linux_amd64.tar.gz -C eksctl eksctl; \
  install -m 0755 eksctl/eksctl /out/eksctl

# The AWS CLI v2: AWS's own installer archive, signed with the AWS CLI team's
# PGP key, which AWS publishes in the CLI's installation guide and which is
# vendored as keyrings/aws-cli.asc and checked against its fingerprint first,
# written as AWS's guide prints it. Ubuntu packages the CLI too, but not at
# every version, and this image follows a consuming repository's exact one.
# The archive carries its own Python, so the installer puts the CLI under
# /usr/local/aws-cli, which is copied out whole. Extraction uses python3
# rather than unzip, which the image does not carry; zipfile drops the
# executable bits, hence the chmod.
COPY keyrings/aws-cli.asc /tmp/releases/aws-cli.asc
ARG AWSCLI_VERSION
# hadolint ignore=DL4006
RUN set -eu; \
  mkdir -p "${HOME}"; \
  want="$(printf '%s' 'FB5D B77F D5C1 18B8 0511 ADA8 A631 0ACC 4672 475C' | tr -d ' ')"; \
  got="$(gpg --show-keys --with-colons aws-cli.asc \
    | awk -F: '$1=="pub"{p=1;next} $1=="fpr"&&p{print $10;p=0;next} {p=0}' \
    | sort | paste -sd, -)"; \
  if [ "${got}" != "${want}" ]; then \
    echo "aws-cli.asc: primary certificates ${got:-none} do not match the reviewed set ${want}" >&2; \
    exit 1; \
  fi; \
  gpg --dearmor < aws-cli.asc > aws-cli.gpg; \
  archive="awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip"; \
  for f in "${archive}" "${archive}.sig"; do \
    curl --proto '=https' --tlsv1.2 -fsSL -o "${f}" "https://awscli.amazonaws.com/${f}"; \
  done; \
  gpgv --keyring ./aws-cli.gpg "${archive}.sig" "${archive}"; \
  python3 -m zipfile -e "${archive}" awscli; \
  chmod -R a+rX,u+x awscli/aws; \
  find awscli/aws -type f \( -name install -o -path '*/dist/*' \) -exec chmod 0755 {} +; \
  awscli/aws/install --install-dir /usr/local/aws-cli --bin-dir /out/aws-bin

# This file, for the version check in the image below, which reads the pins
# from it. Copied here rather than bind mounted from the build context,
# which an SELinux host labels for no container to read.
COPY Dockerfile /tmp/releases/l2-aws-devops.Dockerfile

# hadolint ignore=DL3006
FROM ${L2_IMAGE}

USER 0:0

# terraform from HashiCorp's signed apt repository (the base image already
# checked its key), at the exact version TERRAFORM_VERSION names. The one apt
# package in these images with a version pin, unlike the rule in
# docs/TOOL_SOURCES.md ("No version pins"), because this image follows a
# consuming repository's terraform rather than the newest. HashiCorp numbers
# every package revision 1.
ARG TERRAFORM_VERSION
# hadolint ignore=DL3008
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com resolute main' \
      > /etc/apt/sources.list.d/hashicorp.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends "terraform=${TERRAFORM_VERSION#v}-1" \
  && rm -rf /var/lib/apt/lists/*

COPY --from=atmos /usr/local/bin/atmos /usr/local/bin/atmos
COPY --from=tflint /usr/local/bin/tflint /usr/local/bin/tflint
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --from=helmfile /usr/local/bin/helmfile /usr/local/bin/helmfile
COPY --from=kubeseal /usr/local/bin/kubeseal /usr/local/bin/kubeseal
COPY --from=pluto /pluto /usr/local/bin/pluto
COPY --from=yq /usr/bin/yq /usr/local/bin/yq
COPY --from=releases /out/helm /out/eksctl /usr/local/bin/
COPY --from=releases /usr/local/aws-cli /usr/local/aws-cli
RUN ln -s /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws \
  && ln -s /usr/local/aws-cli/v2/current/bin/aws_completer /usr/local/bin/aws_completer

# Atmos asks GitHub whether a newer release exists and sends usage telemetry;
# L2 has no network for either, and the image is what decides its version.
ENV ATMOS_VERSION_CHECK_ENABLED=false \
    ATMOS_TELEMETRY_ENABLED=false

# Every tool runs, with no network, and each pinned one reports the version
# this file pins: the tag on its FROM line above, or its _VERSION argument.
# A copy out of the wrong path, a binary that needs something this image
# lacks, or a pin moved without the tool moving fails the build here rather
# than a hook in L2. HOME is a scratch directory, so nothing they write on a first run
# stays in the image.
ARG HELM_VERSION
ARG EKSCTL_VERSION
ARG TERRAFORM_VERSION
ARG AWSCLI_VERSION
# hadolint ignore=DL4006
RUN --network=none --mount=type=bind,from=releases,source=/tmp/releases/l2-aws-devops.Dockerfile,target=/tmp/l2-aws-devops.Dockerfile \
  set -eu; \
  export HOME=/tmp/version-check; \
  mkdir -p "${HOME}"; \
  tag() { \
    awk -v stage="$1" '$1 == "FROM" && $3 == "AS" && $4 == stage { ref = $2; sub(/@.*/, "", ref); sub(/.*:/, "", ref); print ref }' \
      /tmp/l2-aws-devops.Dockerfile; \
  }; \
  reports() { \
    want="${1#v}"; shift; \
    [ -n "${want}" ] || { echo "$1: no pinned version found" >&2; exit 1; }; \
    out="$("$@" 2>&1)" || { echo "$*: failed: ${out}" >&2; exit 1; }; \
    pattern="(^|[^0-9.])$(printf '%s' "${want}" | sed 's/\./\\./g')([^0-9.]|\$)"; \
    if ! printf '%s\n' "${out}" | grep -qE "${pattern}"; then \
      echo "$*: reports ${out}, not the pinned ${want}" >&2; \
      exit 1; \
    fi; \
    echo "$1 ${want}"; \
  }; \
  reports "$(tag atmos)" atmos version; \
  reports "$(tag tflint)" tflint --version; \
  reports "$(tag kubectl)" kubectl version --client; \
  reports "$(tag helmfile)" helmfile --version; \
  reports "$(tag kubeseal)" kubeseal --version; \
  reports "$(tag pluto)" pluto version; \
  reports "$(tag yq)" yq --version; \
  reports "${HELM_VERSION}" helm version --short; \
  reports "${EKSCTL_VERSION}" eksctl version; \
  reports "${TERRAFORM_VERSION}" terraform version; \
  reports "${AWSCLI_VERSION}" aws --version; \
  rm -rf "${HOME}"

USER 1000:1000
WORKDIR /workspace

LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="L2 of the ivan-pinatti-labs images for AWS infrastructure repositories: the shared L2 image with atmos, terraform, tflint, the AWS CLI, kubectl, helm, helmfile, eksctl, kubeseal, pluto and yq." \
      org.opencontainers.image.licenses="Apache-2.0"
