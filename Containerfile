FROM quay.io/edge-infrastructure/openshift-appliance@sha256:3cc10f5619a11ca98749e66473db93b0eb69a27544495d09c16e111b1a147080

# Static manifests — baked in at image build time; entrypoint copies to /assets at run time.
# Files that reference the AAP namespace use ${NAMESPACE} as a placeholder; the entrypoint
# substitutes it before copying. Fully static files are copied unchanged.
COPY assets/ /static/
COPY config/ /static/config/
COPY scripts/ /static/scripts/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Defaults — override at run time with -e PARAM=value.
# Build and run both require sudo so the image is in root's store:
#   sudo podman build -t aap-appliance:latest .
#   sudo podman run --rm --privileged --net=host \
#     -e BASE_DOMAIN=... -e RENDEZVOUS_IP=... \
#     -v /path/to/pull-secret.json:/run/secrets/pull-secret:Z \
#     -v /path/to/id_rsa.pub:/run/secrets/ssh-key:Z \
#     -v /path:/assets:Z \
#     aap-appliance:latest
# /run/secrets/pull-secret is required; BASE_DOMAIN and RENDEZVOUS_IP are required and have no defaults.
ENV CLUSTER_NAME=appliance \
    MACHINE_NETWORK=192.168.122.0/24 \
    DISK_SIZE_GB=200 \
    NAMESPACE=aap \
    APPLIANCE_FORMAT=raw

ENTRYPOINT ["/entrypoint.sh"]
