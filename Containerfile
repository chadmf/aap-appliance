FROM quay.io/edge-infrastructure/openshift-appliance@sha256:bff07cb9d68768a2689e1dd9b0fc38fca9ad6cebd34fa16a1d9a319aedd3b20c

# Static manifests — baked in at image build time; entrypoint copies to /assets at run time.
# Files that reference the AAP namespace use ${AAP_NAMESPACE} as a placeholder; the entrypoint
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
    APPLIANCE_CONTENT=aap \
    AAP_NAMESPACE=aap \
    AAP_PRERELEASE=false \
    AO_NAMESPACE=automation-orchestrator \
    AO_PRERELEASE=true \
    APPLIANCE_FORMAT=live-iso \
    DISCONNECTED=true

ENTRYPOINT ["/entrypoint.sh"]
