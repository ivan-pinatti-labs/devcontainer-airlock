# The egress proxy: the only way out to the network for the workbenches and
# L2 engines of every workspace on the host. It joins each workspace's
# internal network, and host/workbench registers the workspace with it.
#
# For each workspace it allows the domains of the egress sets that workspace
# selected (.devcontainer/egress-sets, docs/LAYERS.md "Network"), and for sets whose
# provider publishes address ranges (GitHub, AWS) it also requires that an
# allowed name resolve inside them. Those lists are fetched when the proxy
# starts and every six hours, so they do not go stale.
#
# squid rather than tinyproxy: tinyproxy can only match a host name, and
# checking where the name actually resolves is the point of the ranges.
ARG BASE_IMAGE=localhost/airlock-base:local
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}
USER 0:0
# squid depends on ssl-cert, whose install generates a self signed key pair
# in /etc/ssl. Nothing here serves TLS, and a private key baked into a layer
# is exactly what the secret scan blocks a publish for, so it goes in the same
# layer it was created in.
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install -y --no-install-recommends squid unbound dns-root-data dnscrypt-proxy \
  && rm -f /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem \
  && rm -rf /var/lib/apt/lists/*
COPY squid.conf /etc/squid/squid.conf
COPY unbound.conf /etc/unbound/unbound.conf
COPY dnscrypt-proxy.toml /etc/dnscrypt-proxy/dnscrypt-proxy.toml
COPY sets/ /etc/egress/sets/
COPY bin/egress-refresh bin/egress-reload bin/egress-proxy /usr/local/bin/
RUN install -d -o 1000 -g 1000 /var/lib/egress /run/egress /run/squid \
  && unbound-checkconf /etc/unbound/unbound.conf
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="Egress proxy for the ivan-pinatti-labs workbench: modular allowlists per service, with provider address ranges refreshed automatically." \
      org.opencontainers.image.licenses="Apache-2.0"
CMD ["egress-proxy"]
