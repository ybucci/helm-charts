# Traefik External DNS Controller Helm Chart

This Helm chart deploys the Traefik External DNS Controller, which monitors multiple Traefik LoadBalancer services and automatically updates `external-dns.alpha.kubernetes.io/target` annotations on IngressRoute resources based on dynamic service configurations.

## Features

- 🔄 Automatic external-dns target annotation updates
- 🎯 Support for multiple LoadBalancer services with dynamic configuration
- 📊 Flexible default-based service selection with annotation override support
- 🔧 JSON-based service configuration with intuitive default settings
- 🛡️ Security-focused with proper RBAC permissions
- 📈 Built-in health checks and monitoring support
- 🌐 Multi-environment and multi-region support
- ⚡ Kopf framework-based for efficient Kubernetes event handling

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
helm install traefik-external-dns-controller ./charts/traefik-external-dns-controller
```

### Install with Custom Values

```bash
# Using values file
helm install traefik-external-dns-controller ./charts/traefik-external-dns-controller \
  -f values-example.yaml

# Using dynamic service configuration
helm install traefik-external-dns-controller ./charts/traefik-external-dns-controller \
  --set-string controller.env.servicesConfig='{
    "external": {
      "namespace": "traefik",
      "name": "traefik-external",
      "default": true,
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
| `controller.image.tag` | Container image tag | `2.0.7` |
| `controller.resources.limits.cpu` | CPU limit | `200m` |
| `controller.resources.limits.memory` | Memory limit | `256Mi` |
| `rbac.create` | Create RBAC resources | `true` |
| `serviceAccount.create` | Create service account | `true` |

### Dynamic Service Configuration (Recommended)

The controller supports dynamic service configuration via JSON with **default-based service selection**. This replaces the previous priority-based system with a more intuitive approach:

```yaml
controller:
  env:
    servicesConfig: |
      {
        "external": {
          "namespace": "traefik",
          "name": "traefik-external",
          "default": true,
          "annotations": {
            "traefik.io/external": "true"
          }
        },
        "internal": {
          "namespace": "traefik",
          "name": "traefik-internal",
          "default": false,
          "annotations": {
            "traefik.io/internal": "true"
          }
        },
        "staging": {
          "namespace": "traefik-staging",
          "name": "traefik-staging",
          "default": false,
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
          "default": true,
          "annotations": {
            "traefik.io/region": "us-east"
          }
        },
        "us-west": {
          "namespace": "traefik-us-west",
          "name": "traefik-us-west",
          "default": false,
          "annotations": {
            "traefik.io/region": "us-west"
          }
        },
        "eu-central": {
          "namespace": "traefik-eu",
          "name": "traefik-eu-central",
          "default": false,
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
| `default` | Whether this service is the default choice | No | false |
| `annotations` | Annotations to match on IngressRoutes | No | {} |

### Service Selection Logic

The controller uses the following logic to select which service to use for each IngressRoute:

1. **Explicit Configuration**: If the IngressRoute has `traefik.io/load-balancer-type` annotation, use that service directly
2. **Annotation Matching**: If IngressRoute annotations match any service's annotation patterns, use that service
3. **Default Service**: Use the service marked with `default: true`
4. **Fallback**: If no default is specified, use the first service in the configuration

**Important**: The controller **does not** automatically add `traefik.io/load-balancer-type` annotations to avoid override loops. This allows the logic to work consistently across multiple runs.

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
  traefik.io/internal: "true"      # Matches services configured for internal access
  traefik.io/region: "us-east"     # Matches services configured for this region
  traefik.io/environment: "staging" # Matches services for this environment
```

### Example IngressRoute Configurations

#### External Service (Default)
```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-external
  # No annotations needed - will use default service
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

#### Internal Service (Explicit)
```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-internal
  annotations:
    traefik.io/internal: "true"
spec:
  entryPoints:
    - internal
  routes:
    - match: Host(`my-app.internal.example.com`)
      kind: Rule
      services:
        - name: my-app
          port: 80
```

#### Direct Service Type Selection
```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-specific
  annotations:
    traefik.io/load-balancer-type: "staging"
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`my-app-staging.example.com`)
      kind: Rule
      services:
        - name: my-app
          port: 80
```

## Advanced Examples

### Multi-Environment Setup

Deploy the controller with different environments:

```bash
helm install traefik-external-dns-controller ./charts/traefik-external-dns-controller \
  --set-string controller.env.servicesConfig='{
    "production": {
      "namespace": "traefik-prod",
      "name": "traefik-production",
      "default": true,
      "annotations": {
        "traefik.io/environment": "production"
      }
    },
    "staging": {
      "namespace": "traefik-staging",
      "name": "traefik-staging",
      "default": false,
      "annotations": {
        "traefik.io/environment": "staging"
      }
    }
  }'
```

### IngressRoute Examples

#### Environment-specific routing:

```yaml
# Production IngressRoute (uses default)
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-production
  # No annotations - will use default production service
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
# Staging IngressRoute (explicit)
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
# US East IngressRoute (uses default)
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app-us-east
  # No annotations - will use default us-east service
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
# EU Central IngressRoute (explicit)
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

## RBAC Permissions

The controller requires the following Kubernetes permissions:

- **Services**: `get`, `list`, `watch` - To monitor LoadBalancer services
- **IngressRoutes**: `get`, `list`, `watch`, `patch`, `update` - To update external-dns annotations
- **Events**: `create`, `patch` - For logging events
- **Secrets**: `get`, `list`, `watch`, `create`, `update`, `patch` - For Kopf framework state
- **Leases**: `get`, `list`, `watch`, `create`, `update`, `patch` - For Kopf coordination
- **CustomResourceDefinitions**: `get`, `list`, `watch` - For Kopf CRD discovery

All permissions are automatically configured when `rbac.create: true` (default).

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
kubectl auth can-i list customresourcedefinitions --as=system:serviceaccount:default:traefik-external-dns-controller
```

### Common Issues

1. **Controller not starting**: Check RBAC permissions and service references
2. **IngressRoutes not updated**: Verify service references and annotations
3. **Permission denied**: Ensure RBAC is enabled and properly configured
4. **CRD discovery errors**: Verify customresourcedefinitions permissions are granted
5. **User detection errors**: Controller runs as root to avoid uid issues

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

## Migration from Priority-Based Configuration

If you're upgrading from a priority-based configuration, update your service configuration:

**Old (priority-based)**:
```yaml
servicesConfig: |
  {
    "external": {
      "namespace": "traefik",
      "name": "traefik-external",
      "priority": 100,
      "annotations": {"traefik.io/external": "true"}
    },
    "internal": {
      "namespace": "traefik",
      "name": "traefik-internal", 
      "priority": 90,
      "annotations": {"traefik.io/internal": "true"}
    }
  }
```

**New (default-based)**:
```yaml
servicesConfig: |
  {
    "external": {
      "namespace": "traefik",
      "name": "traefik-external",
      "default": true,
      "annotations": {"traefik.io/external": "true"}
    },
    "internal": {
      "namespace": "traefik",
      "name": "traefik-internal",
      "default": false,
      "annotations": {"traefik.io/internal": "true"}
    }
  }
```

## Upgrading

```bash
# Upgrade to latest version
helm upgrade traefik-external-dns-controller ./charts/traefik-external-dns-controller

# Upgrade with new values
helm upgrade traefik-external-dns-controller ./charts/traefik-external-dns-controller \
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