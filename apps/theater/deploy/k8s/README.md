# //apps/theater/deploy/k8s — placeholder for when you move off raw Docker.
#
# Recommended shape (manifests TBD):
#   theater-Namespace.yaml
#   theater-Deployment.yaml      # hostNetwork: true, single replica
#   theater-PersistentVolume.yaml # local PV for /root/.config/theater
#   theater-PersistentVolumeClaim.yaml
#   theater-Service.yaml         # ClusterIP, optional — host network exposes it directly
#   theater-Ingress.yaml         # if you want a friendlier URL than IP:port
#
# Constraints from the protocol layer:
#   - hostNetwork: true required for Wake-on-LAN broadcasts.
#   - Single replica only — there's one source of truth and no coordination.
#   - PV must be node-local; credentials are tied to the pairing host.
#
# Add manifests here once your k8s install is online. Until then, the docker
# run command in RUNBOOK.md §5b is the deployment path.
