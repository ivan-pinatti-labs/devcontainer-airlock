# The package mirror's gate, and the provisioning of its backend.
#
# Clients talk to the gate only, at fixed paths per ecosystem and a registry
# port per upstream; which backend answers is a routes file (routes/), so a
# different backend (Pulp, say) changes no client. On the way through it
# takes out npm, PyPI and Go versions that OSV lists as malicious, and
# optionally versions younger than MIN_AGE_DAYS. docs/LAYERS.md, "Package
# mirror".
#
# The same image runs backends/<backend>/provision once each time the backend
# starts: read through only, nothing hosted, anonymous read only.
ARG BASE_IMAGE=localhost/airlock-base:local
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}
USER 0:0
COPY lib/ /usr/local/lib/mirror-gate/
COPY bin/mirror-gate /usr/local/bin/
COPY routes/ /etc/mirror-gate/routes/
COPY backends/ /usr/local/lib/mirror-gate/backends/
# The filter cases: a change that lets a malicious version through fails the
# build.
COPY test_gate.py /tmp/test_gate.py
RUN python3 /tmp/test_gate.py && rm /tmp/test_gate.py \
  && install -d -o 1000 -g 1000 /var/lib/mirror-gate
USER 1000:1000
EXPOSE 8081 5000 5001
LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="Gate of the package mirror: fixed paths per ecosystem, OSV malicious package filter, backend selected by a routes file." \
      org.opencontainers.image.licenses="Apache-2.0"
CMD ["mirror-gate"]
