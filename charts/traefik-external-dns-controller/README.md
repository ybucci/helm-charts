# Traefik External DNS Controller Helm Chart

This Helm chart deploys the Traefik External DNS Controller, which monitors multiple Traefik LoadBalancer services and automatically updates `external-dns.alpha.kubernetes.io/target` annotations on IngressRoute resources based on dynamic service configurations.

## Features

- 🔄 Automatic external-dns target annotation updates
- 🎯 Support for multiple LoadBalancer services with dynamic configuration
- 📊 Flexible configuration via annotations and priority-based routing
- 🔧 JSON-based service configuration
- 🛡️ Security-focused with non-root containers
- 📈 Built-in health checks and monitoring support
- 🌐 Multi-environment and multi-region support

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
# Using values file
helm install traefik-external-dns-controller ./traefik-external-dns-controller \
  -f values-example.yaml

# Using dynamic service configuration
helm install traefik-external-dns-controller ./traefik-external-dns-controller \
  --set-string controller.env.servicesConfig='{
    "external": {
      "namespace": "traefik",
      "name": "traefik-external",
      "priority": 100,
      "annotations": {
        "traefik.io/external": "true"
      }
    }
  }'
```

## Configuration

### Key Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `controller.env.servicesConfig` | JSON configuration for multiple services | `""` |
| `controller.image.repository` | Container image repository | `ybucci/traefik-external-dns-controller` |
| `controller.image.tag` | Container image tag | `2.0.0` |
| `controller.resources.limits.cpu` | CPU limit | `200m` |
| `controller.resources.limits.memory` | Memory limit | `256Mi` |
| `replicaCount` | Number of replicas | `1` |
| `rbac.create` | Create RBAC resources | `true` |
| `serviceAccount.create` | Create service account | `true` |

### Dynamic Service Configuration (Recommended)

The controller now supports dynamic service configuration via JSON, allowing multiple services with priority-based routing:

```yaml
controller:
  env:
    servicesConfig: |
      {
        "external": {
          "namespace": "traefik",
          "name": "traefik-external",
          "priority": 100,
          "annotations": {
            "traefik.io/external": "true"
          }
        },
        "internal": {
          "namespace": "traefik",
          "name": "traefik-internal",
          "priority": 90,
          "annotations": {
            "traefik.io/internal": "true"
          }
        },
        "staging": {
          "namespace": "traefik-staging",
          "name": "traefik-staging",
          "priority": 80,
          "annotations": {
            "traefik.io/environment": "staging"
          }
        }
      }
```

#### Multi-Region Configuration Example

```yaml
controller:
  env:
    servicesConfig: |
      {
        "us-east": {
          "namespace": "traefik-us-east",
          "name": "traefik-us-east",
          "priority": 100,
          "annotations": {
            "traefik.io/region": "us-east"
          }
        },
        "us-west": {
          "namespace": "traefik-us-west",
          "name": "traefik-us-west",
          "priority": 90,
          "annotations": {
            "traefik.io/region": "us-west"
          }
        },
        "eu-central": {
          "namespace": "traefik-eu",
          "name": "traefik-eu-central",
          "priority": 80,
          "annotations": {
            "traefik.io/region": "eu-central"
          }
        }
      }
```

#### Service Configuration Properties

| Property | Description | Required | Default |
|----------|-------------|----------|---------|
| `namespace` | Namespace of the LoadBalancer service | Yes | - |
| `name` | Name of the LoadBalancer service | Yes | - |
| `priority` | Priority for service selection (lower = higher priority) | No | 100 |
| `annotations` | Annotations to match on IngressRoutes | No | {} |

### Service Selection Logic

The controller uses the following prioritized logic to select which service to use for each IngressRoute:

1. **Direct Service Type**: If the IngressRoute has `traefik.io/load-balancer-type` annotation, use that service directly
2. **Annotation Matching**: If IngressRoute annotations match any service's annotation patterns, use the highest priority match
3. **Default**: Use the service with the highest priority (lowest number)

### IngressRoute Annotations

The controller recognizes the following annotations on IngressRoute resources:

#### Direct Service Selection
```yaml
annotations:
  traefik.io/load-balancer-type: "external"  # Direct service selection
```

#### Custom Annotation Matching
```yaml
annotations:
  traefik.io/external: "true"      # Matches services with this annotation
  traefik.io/region: "us-east"     # Matches services configured for this region
  traefik.io/environment: "staging" # Matches services for this environment
```

### Example IngressRoute

```yaml
apiVersion: traefik.io/v1alpha1
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

## Advanced Examples

### Multi-Environment Setup

Deploy the controller with different environments:

```bash
helm install traefik-external-dns-controller ./traefik-external-dns-controller \
  --set-string controller.env.servicesConfig='{
    "production": {
      "namespace": "traefik-prod",
      "name": "traefik-production",
      "priority": 100,
      "annotations": {
        "traefik.io/environment": "production"
      }
    },
    "staging": {
      "namespace": "traefik-staging",
      "name": "traefik-staging",
      "priority": 90,
      "annotations": {
        "traefik.io/environment": "staging"
      }
    }
  }'
```

### IngressRoute Examples

#### Environment-specific routing:

```yaml
# Production IngressRoute
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-production
  annotations:
    traefik.io/environment: "production"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`my-app.example.com`)
      kind: Rule
      services:
        - name: my-app-prod
          port: 80

---
# Staging IngressRoute
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-staging
  annotations:
    traefik.io/environment: "staging"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`my-app-staging.example.com`)
      kind: Rule
      services:
        - name: my-app-staging
          port: 80
```

#### Multi-region routing:

```yaml
# US East IngressRoute
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-us-east
  annotations:
    traefik.io/region: "us-east"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`us-east.my-app.example.com`)
      kind: Rule
      services:
        - name: my-app
          port: 80

---
# EU Central IngressRoute
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-eu-central
  annotations:
    traefik.io/region: "eu-central"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`eu.my-app.example.com`)
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