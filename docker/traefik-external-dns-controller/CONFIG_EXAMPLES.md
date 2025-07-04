# Dynamic Service Configuration Examples

Este controller suporta configuração dinâmica de serviços através da variável de ambiente `SERVICES_CONFIG`, permitindo configurar quantos serviços forem necessários.

## Configuração Básica (JSON)

### Exemplo 1: Configuração Simples com External e Internal

```json
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
  }
}
```

### Exemplo 2: Configuração Multi-Ambiente

```json
{
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
  },
  "development": {
    "namespace": "traefik-dev",
    "name": "traefik-dev",
    "priority": 80,
    "annotations": {
      "traefik.io/environment": "development"
    }
  }
}
```

### Exemplo 3: Configuração por Região

```json
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

## Configuração no Kubernetes

### Deployment/ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: traefik-external-dns-controller-config
  namespace: traefik
data:
  services-config.json: |
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
      }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: traefik-external-dns-controller
  namespace: traefik
spec:
  template:
    spec:
      containers:
      - name: controller
        image: traefik-external-dns-controller:latest
        env:
        - name: SERVICES_CONFIG
          valueFrom:
            configMapKeyRef:
              name: traefik-external-dns-controller-config
              key: services-config.json
```

### Helm Values

```yaml
# values.yaml
servicesConfig:
  external:
    namespace: traefik
    name: traefik-external
    priority: 100
    annotations:
      traefik.io/external: "true"
  internal:
    namespace: traefik
    name: traefik-internal
    priority: 90
    annotations:
      traefik.io/internal: "true"
```

## Uso nos IngressRoutes

### Método 1: Annotation Direta

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app
  annotations:
    traefik.io/load-balancer-type: "external"  # Usa diretamente o serviço "external"
spec:
  # ... resto da configuração
```

### Método 2: Annotations que Fazem Match

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app
  annotations:
    traefik.io/external: "true"  # Faz match com o serviço que tem esta annotation
spec:
  # ... resto da configuração
```

### Método 3: Automatico (Usa Prioridade)

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: my-app
  # Sem annotations específicas - usa o serviço com maior prioridade
spec:
  # ... resto da configuração
```

## Lógica de Seleção

1. **Annotation Direta**: Se o IngressRoute tem `traefik.io/load-balancer-type`, usa esse serviço diretamente
2. **Match por Annotations**: Se as annotations do IngressRoute fazem match com as annotations configuradas de um serviço, usa o serviço com maior prioridade (menor número)
3. **Padrão**: Se nenhum match, usa o serviço com maior prioridade (menor número)



## Parâmetros de Configuração

### Campos Obrigatórios
- `namespace`: Namespace do serviço LoadBalancer
- `name`: Nome do serviço LoadBalancer

### Campos Opcionais
- `priority`: Prioridade do serviço (menor = maior prioridade, padrão: 100)
- `annotations`: Annotations que devem fazer match no IngressRoute para usar este serviço 