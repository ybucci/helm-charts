# Traefik External DNS Controller Helm Chart

This Helm chart deploys the Traefik External DNS Controller, which monitors Traefik LoadBalancer services and automatically updates `external-dns.alpha.kubernetes.io/target` annotations on IngressRoute resources based on internal/external routing configurations.

## Features

- 🔄 Automatic external-dns target annotation updates
- 🎯 Support for both internal and external LoadBalancer services
- 📊 Flexible configuration via annotations
- 🔧 Configurable via Helm values
- 🛡️ Security-focused with non-root containers
- 📈 Built-in health checks and monitoring support

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- Traefik 2.x deployed in your cluster
- LoadBalancer service(s) for Traefik

## Installation

### Add the Helm Repository (if applicable)

```bash
# If you have a Helm repository
helm repo add traefik-external-dns-controller https://your-helm-repo.com
helm repo update
```

### Install from Local Chart

```bash
# Clone the repository
git clone https://github.com/ybucci/traefik-external-dns-controller.git
cd traefik-external-dns-controller

# Install the chart
helm install traefik-external-dns-controller ./traefik-external-dns-controller
```

### Install with Custom Values

```bash
# Basic installation with external LoadBalancer only
helm install traefik-external-dns-controller ./traefik-external-dns-controller \
  --set controller.env.externalServiceRef="traefik/traefik-external"

# Dual LoadBalancer setup
helm install traefik-external-dns-controller ./traefik-external-dns-controller \
  --set controller.env.externalServiceRef="traefik/traefik-external" \
  --set controller.env.internalServiceRef="traefik/traefik-internal"

# Using values file
helm install traefik-external-dns-controller ./traefik-external-dns-controller \
  -f values-example.yaml
```

## Configuration

### Key Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `controller.env.externalServiceRef` | External LoadBalancer service reference | `""` |
| `controller.env.internalServiceRef` | Internal LoadBalancer service reference | `""` |
| `controller.image.repository` | Container image repository | `ybucci/traefik-external-dns-controller` |
| `controller.image.tag` | Container image tag | `1.0.0` |
| `controller.resources.limits.cpu` | CPU limit | `200m` |
| `controller.resources.limits.memory` | Memory limit | `256Mi` |
| `replicaCount` | Number of replicas | `1` |
| `rbac.create` | Create RBAC resources | `true` |
| `serviceAccount.create` | Create service account | `true` |

### Service Reference Format

The service reference should be in the format `namespace/service-name`:

```yaml
controller:
  env:
    externalServiceRef: "traefik/traefik-external"
    internalServiceRef: "traefik/traefik-internal"
```

### IngressRoute Annotations

The controller recognizes the following annotations on IngressRoute resources:

#### Primary Annotations
```yaml
annotations:
  traefik.io/load-balancer-type: "external"  # or "internal"
```

#### Legacy Annotations (still supported)
```yaml
annotations:
  traefik.io/external: "true"    # for external routing
  traefik.io/internal: "true"    # for internal routing
```

### Example IngressRoute

```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-external
  annotations:
    traefik.io/load-balancer-type: "external"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`my-app.example.com`)
      kind: Rule
      services:
        - name: my-app
          port: 80
```

## Monitoring

### Health Checks

The controller includes built-in health checks:

```yaml
controller:
  env:
    enableHealthCheck: true
    healthCheckPort: 8080
```

### Prometheus Metrics (Optional)

Enable Prometheus metrics collection:

```yaml
monitoring:
  prometheus:
    enabled: true
    port: 9090
    path: /metrics
  serviceMonitor:
    enabled: true
    namespace: monitoring
```

## Troubleshooting

### Check Controller Status

```bash
# Check pod status
kubectl get pods -l app.kubernetes.io/name=traefik-external-dns-controller

# View logs
kubectl logs -l app.kubernetes.io/name=traefik-external-dns-controller -f

# Check RBAC permissions
kubectl auth can-i get services --as=system:serviceaccount:default:traefik-external-dns-controller
```

### Common Issues

1. **Controller not starting**: Check RBAC permissions and service references
2. **IngressRoutes not updated**: Verify service references and annotations
3. **Permission denied**: Ensure RBAC is enabled and properly configured

### Debug Mode

Enable debug mode for detailed logging:

```yaml
controller:
  env:
    kopfLogLevel: "DEBUG"
config:
  debug: true
```

### Dry Run Mode

Test configuration without applying changes:

```yaml
config:
  dryRun: true
```

## Upgrading

```bash
# Upgrade to latest version
helm upgrade traefik-external-dns-controller ./traefik-external-dns-controller

# Upgrade with new values
helm upgrade traefik-external-dns-controller ./traefik-external-dns-controller \
  -f new-values.yaml
```

## Uninstallation

```bash
helm uninstall traefik-external-dns-controller
```

## Values Reference

See [values.yaml](values.yaml) for all available configuration options.

For configuration examples, see [values-example.yaml](values-example.yaml).

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with different configurations
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Support

For issues and questions:
- GitHub Issues: https://github.com/ybucci/traefik-external-dns-controller/issues
- Documentation: https://github.com/ybucci/traefik-external-dns-controller 