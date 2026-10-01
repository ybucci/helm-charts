import os
import logging
import warnings
import sys
import io
import kopf
import kubernetes.config
from kubernetes.client import ApiClient, CustomObjectsApi, CoreV1Api
from kubernetes import watch
import threading
import time
import json
from queue import Queue
from http.server import HTTPServer, BaseHTTPRequestHandler

# Supported Traefik API groups (old and new)
TRAEFIK_API_GROUPS = ['traefik.containo.us', 'traefik.io']
TRAEFIK_VERSION = 'v1alpha1'

# Traefik route kinds handled by this controller.
# IngressRoute matches on Host(), so external-dns can usually derive the hostname
# from the rule. IngressRouteTCP matches on HostSNI() - and with HostSNI(`*`)
# there is no hostname at all - while IngressRouteUDP has no host matcher of any
# kind, so both rely exclusively on the
# external-dns.alpha.kubernetes.io/hostname annotation.
TRAEFIK_PLURALS = ['ingressroutes', 'ingressroutetcps', 'ingressrouteudps']

# Plurals that require an explicit hostname annotation to produce a DNS record.
HOSTNAME_ANNOTATION_REQUIRED = {'ingressroutetcps', 'ingressrouteudps'}

# Plurals where the Cloudflare proxy makes sense (HTTP/HTTPS only).
CLOUDFLARE_PROXIED_PLURALS = {'ingressroutes'}

# external-dns annotation prefix. Older external-dns releases read
# 'external-dns.alpha.kubernetes.io/' (default here, backward compatible); newer
# ones (>= v0.20, e.g. v0.22) only read 'external-dns.kubernetes.io/' and ignore
# the alpha prefix. Set EXTERNAL_DNS_ANNOTATION_PREFIX to match the external-dns
# version in use.
EXTERNAL_DNS_ANNOTATION_PREFIX = os.getenv(
    'EXTERNAL_DNS_ANNOTATION_PREFIX', 'external-dns.alpha.kubernetes.io/'
).strip() or 'external-dns.alpha.kubernetes.io/'
if not EXTERNAL_DNS_ANNOTATION_PREFIX.endswith('/'):
    EXTERNAL_DNS_ANNOTATION_PREFIX += '/'

HOSTNAME_ANNOTATION = f'{EXTERNAL_DNS_ANNOTATION_PREFIX}hostname'
TARGET_ANNOTATION = f'{EXTERNAL_DNS_ANNOTATION_PREFIX}target'
CLOUDFLARE_PROXIED_ANNOTATION = f'{EXTERNAL_DNS_ANNOTATION_PREFIX}cloudflare-proxied'

active_api_groups = []  # Will be populated at startup
# {api_group: [plural, ...]} - which route kinds exist per detected API group
active_resources = {}

# Custom stderr filter to suppress CRD warnings
class FilteredStderr:
    """Wrapper for stderr that filters out CRD warning messages."""
    def __init__(self, original_stderr):
        self.original_stderr = original_stderr
        self.buffer = []
        
    def write(self, text):
        # Filter out CRD warning messages
        if 'Unresolved resources cannot be served' in text:
            return
        if 'try creating their CRDs' in text:
            return
        self.original_stderr.write(text)
        
    def flush(self):
        self.original_stderr.flush()
        
    def fileno(self):
        return self.original_stderr.fileno()

# Apply stderr filter
sys.stderr = FilteredStderr(sys.stderr)

# Dynamic Service Configuration Support
# ====================================
# This controller supports dynamic service configuration through the SERVICES_CONFIG environment variable.
# 
# Example SERVICES_CONFIG JSON format:
# {
#   "external": {
#     "namespace": "traefik",
#     "name": "traefik-external",
#     "priority": 100,
#     "annotations": {
#       "traefik.io/external": "true"
#     }
#   },
#   "internal": {
#     "namespace": "traefik",
#     "name": "traefik-internal", 
#     "priority": 90,
#     "annotations": {
#       "traefik.io/internal": "true"
#     }
#   },
#   "staging": {
#     "namespace": "traefik-staging",
#     "name": "traefik-staging",
#     "priority": 80,
#     "annotations": {
#       "traefik.io/environment": "staging"
#     }
#   }
# }
#
# Service selection logic:
# 1. If the route has "traefik.io/load-balancer-type" annotation, use that service directly
# 2. If the route annotations match any service's annotation patterns, use highest priority match
# 3. Otherwise, use the service with highest priority (lowest number)
#
# Supported route kinds: IngressRoute, IngressRouteTCP and IngressRouteUDP.
# TCP/UDP routes are only processed when they carry an
# external-dns.alpha.kubernetes.io/hostname annotation, since HostSNI(`*`) (TCP) and
# the absence of any host matcher (UDP) leave no hostname to derive.

# 1. Disable warnings
warnings.filterwarnings('ignore', module='kopf._core.reactor.running')

# 2. Robust logging configuration
class SimpleFormatter(logging.Formatter):
    def format(self, record):
        return f"[{self.formatTime(record)}] [{record.levelname}] - {record.getMessage()}"

logger = logging.getLogger('external-dns-controller')
logger.handlers.clear()
logger.setLevel(logging.INFO)
logger.propagate = False

handler = logging.StreamHandler()
handler.setFormatter(SimpleFormatter(datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(handler)

# 3. Custom filter to suppress specific CRD warnings
class SuppressCRDWarnings(logging.Filter):
    """Filter to suppress 'Unresolved resources cannot be served' warnings."""
    def filter(self, record):
        message = record.getMessage()
        # Suppress CRD warnings for API groups we're handling dynamically
        if 'Unresolved resources cannot be served' in message:
            return False
        return True

# 4. Silence other loggers and apply custom filter
for lib in ['kubernetes', 'urllib3', 'kopf', 'asyncio']:
    logging.getLogger(lib).handlers = []
    logging.getLogger(lib).propagate = False
    logging.getLogger(lib).setLevel(logging.WARNING)

# Apply custom filter to root logger to catch all CRD warnings
logging.root.addFilter(SuppressCRDWarnings())
# Also apply to kopf's observation logger specifically
logging.getLogger('kopf.reactor.observation').addFilter(SuppressCRDWarnings())

# Cache to track last updates and health
update_queue = Queue()
last_updated = {}
last_healthy_time = time.time()
service_watch_active = False
# Change to support multiple services
service_hostnames = {}  # {service_type: hostname}
service_configs = {}    # {service_type: {namespace: ns, name: name}}

def update_health():
    """Update the last healthy timestamp."""
    global last_healthy_time
    last_healthy_time = time.time()
    logger.debug("Health timestamp updated")

def detect_traefik_api_groups():
    """Detect which Traefik API groups and route kinds are available in the cluster."""
    global active_api_groups, active_resources
    active_api_groups = []
    active_resources = {}

    api = CustomObjectsApi()
    for group in TRAEFIK_API_GROUPS:
        available_plurals = []
        for plural in TRAEFIK_PLURALS:
            try:
                api.list_cluster_custom_object(
                    group=group,
                    version=TRAEFIK_VERSION,
                    plural=plural,
                    limit=1
                )
                available_plurals.append(plural)
            except Exception as e:
                logger.debug(f"Resource {group}/{TRAEFIK_VERSION}/{plural} not available: {str(e)}")

        if available_plurals:
            active_api_groups.append(group)
            active_resources[group] = available_plurals
            logger.info(f"Detected Traefik API group: {group}/{TRAEFIK_VERSION} (resources: {', '.join(available_plurals)})")

    if not active_api_groups:
        logger.error(f"No Traefik API groups detected! Make sure Traefik CRDs are installed.")
    else:
        logger.info(f"Active Traefik API groups: {', '.join(active_api_groups)}")

    return active_api_groups

def kind_for_plural(plural):
    """Human-readable kind name for log messages."""
    return {
        'ingressroutes': 'IngressRoute',
        'ingressroutetcps': 'IngressRouteTCP',
        'ingressrouteudps': 'IngressRouteUDP',
    }.get(plural, plural)

def is_resource_active(group, plural):
    """Check whether a given API group serves a given route kind."""
    return plural in active_resources.get(group, [])

def groups_for_plural(plural):
    """List the active API groups that serve a given route kind."""
    return [group for group in active_api_groups if is_resource_active(group, plural)]

def parse_service_config():
    """Parse service configuration from environment variables."""
    configs = {}
    
    # Parse dynamic service configuration from JSON
    services_config = os.getenv('SERVICES_CONFIG', '')
    if services_config:
        try:
            services_data = json.loads(services_config)
            for service_id, service_config in services_data.items():
                if 'namespace' in service_config and 'name' in service_config:
                    configs[service_id] = {
                        'namespace': service_config['namespace'],
                        'name': service_config['name'],
                        'priority': service_config.get('priority', 100),
                        'annotations': service_config.get('annotations', {})
                    }
                    logger.info(f"Service '{service_id}' configured: {service_config['namespace']}/{service_config['name']} (priority: {configs[service_id]['priority']})")
                else:
                    logger.error(f"Invalid service configuration for '{service_id}': missing namespace or name")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in SERVICES_CONFIG: {e}")
        except Exception as e:
            logger.error(f"Error parsing SERVICES_CONFIG: {e}")
    
    return configs

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    global service_configs
    
    logger.info("Controller startup initiated")
    
    settings.persistence.finalizer = None
    settings.watching.clusterwide = True
    settings.posting.enabled = False
    settings.watching.server_timeout = 60
    settings.watching.reconnect_backoff = 1.0
    
    try:
        kubernetes.config.load_incluster_config()
        logger.info("In-cluster configuration loaded")
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
        logger.info("Local configuration (kubeconfig) loaded")
    
    # Detect available Traefik API groups
    detect_traefik_api_groups()
    
    if not active_api_groups:
        logger.error("No Traefik API groups available! Cannot proceed.")
        return
    
    # Parse service configurations
    service_configs = parse_service_config()
    
    if not service_configs:
        logger.error("No service configurations found! Set SERVICES_CONFIG environment variable")
        return
    
    logger.info("Controller started successfully | Monitoring services: %s", 
                ", ".join([f"{k}={v['namespace']}/{v['name']}" for k, v in service_configs.items()]))
    
    update_health()

def format_lb_targets(ingress_list):
    """Join every hostname/IP in a LoadBalancer ingress list into a comma-separated
    target string. external-dns natively supports comma-separated targets to create
    multiple A/CNAME records, which is what we need once a Service has more than one
    LoadBalancer entry (e.g. one per node in a multi-node cluster)."""
    if not ingress_list:
        return None
    targets = [ing.hostname or ing.ip for ing in ingress_list if (ing.hostname or ing.ip)]
    return ','.join(targets) if targets else None

def get_lb_hostname(service_type):
    """Get hostname(s) for a specific service type."""
    if service_type not in service_configs:
        logger.error(f"Service type '{service_type}' not configured")
        return None

    try:
        config = service_configs[service_type]
        svc = CoreV1Api().read_namespaced_service(
            name=config['name'],
            namespace=config['namespace']
        )
        if svc.status.load_balancer.ingress:
            hostname = format_lb_targets(svc.status.load_balancer.ingress)
            logger.debug(f"Load balancer hostname obtained for {service_type}: {hostname}")
            return hostname
    except Exception as e:
        logger.error(f"Error obtaining load balancer hostname for {service_type}: {str(e)}")
    return None

def determine_service_type(ingress_route):
    """Determine which service type to use for a Traefik route (IngressRoute,
    IngressRouteTCP or IngressRouteUDP)."""
    annotations = ingress_route.get('metadata', {}).get('annotations', {})
    
    # Check for explicit load-balancer-type annotation
    lb_type = annotations.get('traefik.io/load-balancer-type', '').lower()
    if lb_type and lb_type in service_configs:
        return lb_type
    
    # Check for service-specific annotations
    matching_services = []
    for service_id, service_config in service_configs.items():
        service_annotations = service_config.get('annotations', {})
        matches = True
        
        # Check if all required annotations match
        for key, value in service_annotations.items():
            if annotations.get(key, '').lower() != value.lower():
                matches = False
                break
        
        if matches and service_annotations:  # Only consider if there are annotations to match
            matching_services.append(service_id)
    
    # If we found matching services, return the first one
    if matching_services:
        return matching_services[0]
    
    # Use default service if no specific annotations match
    if service_configs:
        for service_id, config in service_configs.items():
            if config.get('default', False):
                return service_id
        
        # Fallback: if no default is explicitly set, use the first service
        return list(service_configs.keys())[0]
    
    return None

def update_ingress_route(name, namespace, hostname, service_type, plural="ingressroutes"):
    """Update a Traefik route with hostname and service type information."""
    api = CustomObjectsApi()

    # Try each active API group until one succeeds
    for group in groups_for_plural(plural):
        try:
            current = api.get_namespaced_custom_object(
                group=group,
                version=TRAEFIK_VERSION,
                namespace=namespace,
                plural=plural,
                name=name
            )

            resource_key = f"{plural}/{namespace}/{name}"
            current_time = time.time()
            if resource_key in last_updated and (current_time - last_updated[resource_key]) < 5:
                logger.debug(f"Ignoring redundant update for {resource_key}")
                return False

            # Build annotations
            annotations = current.get('metadata', {}).get('annotations', {})
            annotations[TARGET_ANNOTATION] = hostname
            # Add cloudflare-proxied annotation if it doesn't exist.
            # Only meaningful for HTTP routes - Cloudflare cannot proxy raw TCP/UDP here.
            if plural in CLOUDFLARE_PROXIED_PLURALS and CLOUDFLARE_PROXIED_ANNOTATION not in annotations:
                annotations[CLOUDFLARE_PROXIED_ANNOTATION] = 'true'
            # Note: Do not add traefik.io/load-balancer-type to avoid overriding explicit configurations

            patch = {
                'metadata': {
                    'annotations': annotations
                }
            }

            api.patch_namespaced_custom_object(
                group=group,
                version=TRAEFIK_VERSION,
                namespace=namespace,
                plural=plural,
                name=name,
                body=patch
            )

            last_updated[resource_key] = current_time
            logger.info(f"{kind_for_plural(plural)} {namespace}/{name} updated with {service_type} hostname: {hostname} (API group: {group})")
            update_health()
            return True
        except Exception as e:
            error_str = str(e)
            # Check if it's a 404 error (resource not found) - try next API group or silently ignore
            if "Not Found" in error_str or "not found" in error_str or "404" in error_str or "(404)" in error_str:
                continue  # Try next API group
            else:
                logger.error(f"Failed to update {kind_for_plural(plural)} {namespace}/{name} with API group {group}: {error_str}")
                continue  # Try next API group

    # If we get here, all API groups failed
    return False

def has_required_hostname(item, plural):
    """IngressRouteTCP matches on HostSNI (often HostSNI(`*`)) and IngressRouteUDP has
    no host matcher at all, so external-dns has no hostname to derive from the rule.
    For those kinds, only act when the hostname annotation is present."""
    if plural not in HOSTNAME_ANNOTATION_REQUIRED:
        return True
    annotations = item.get('metadata', {}).get('annotations', {})
    return bool(annotations.get(HOSTNAME_ANNOTATION))

def sync_all_ingress_routes(service_type, new_hostname):
    """Sync all Traefik routes of a specific service type with the new hostname."""
    api = CustomObjectsApi()
    updated_count = 0

    # Try each active API group / route kind
    for group in active_api_groups:
        for plural in active_resources.get(group, []):
            try:
                ingress_routes = api.list_cluster_custom_object(
                    group=group,
                    version=TRAEFIK_VERSION,
                    plural=plural
                )

                for item in ingress_routes.get('items', []):
                    name = item['metadata']['name']
                    namespace = item['metadata']['namespace']

                    # Determine if this route should use this service type
                    determined_type = determine_service_type(item)
                    if determined_type != service_type:
                        continue

                    if not has_required_hostname(item, plural):
                        logger.debug(f"Skipping {kind_for_plural(plural)} {namespace}/{name}: no {HOSTNAME_ANNOTATION} annotation")
                        continue

                    current_target = item['metadata'].get('annotations', {}).get(TARGET_ANNOTATION)
                    if current_target != new_hostname:
                        logger.info(f"Updating via sync {kind_for_plural(plural)} {namespace}/{name} ({service_type}) from {current_target} to {new_hostname}")
                        if update_ingress_route(name, namespace, new_hostname, service_type, plural):
                            updated_count += 1
            except Exception as e:
                logger.error(f"Failed to sync {kind_for_plural(plural)} for {service_type} with API group {group}: {str(e)}")
                continue

    logger.info(f"Synchronized {updated_count} Traefik routes for {service_type} LoadBalancer")
    update_health()

def handle_ingressroute_event(name, namespace, body, api_group, plural="ingressroutes"):
    """Handle Traefik route events (common logic for all API groups and route kinds)."""
    # Skip if this API group / route kind is not active
    if not is_resource_active(api_group, plural):
        return

    kind = kind_for_plural(plural)
    logger.debug(f"Event received for {kind}: {namespace}/{name} (API group: {api_group})")

    if not has_required_hostname(body, plural):
        logger.debug(f"Skipping {kind} {namespace}/{name}: no {HOSTNAME_ANNOTATION} annotation")
        return

    # Determine which service type this route should use
    service_type = determine_service_type(body)
    if not service_type:
        logger.warning(f"Could not determine service type for {kind} {namespace}/{name}")
        return

    # Get hostname for the determined service type
    hostname = get_lb_hostname(service_type)
    if not hostname:
        logger.warning(f"No hostname available for {service_type} LoadBalancer for {kind} {namespace}/{name}")
        return

    # Check current state
    annotations = body['metadata'].get('annotations', {})
    current_target = annotations.get(TARGET_ANNOTATION)
    current_type = annotations.get('traefik.io/load-balancer-type')
    current_cloudflare_proxied = annotations.get(CLOUDFLARE_PROXIED_ANNOTATION)

    # Determine if update is actually needed
    needs_update = False
    update_reason = ""

    # Check if hostname needs to be updated
    if current_target != hostname:
        needs_update = True
        update_reason = f"hostname mismatch (current: {current_target}, expected: {hostname})"

    # Check if cloudflare-proxied annotation is missing (HTTP routes only)
    elif plural in CLOUDFLARE_PROXIED_PLURALS and current_cloudflare_proxied is None:
        needs_update = True
        update_reason = "cloudflare-proxied annotation missing"

    # Only check load-balancer-type if it's explicitly set and different
    elif current_type is not None and current_type != service_type:
        needs_update = True
        update_reason = f"explicit load-balancer-type mismatch (current: {current_type}, expected: {service_type})"

    # Perform update only if actually needed
    if needs_update:
        logger.info(f"Updating via event {kind} {namespace}/{name} with {service_type} hostname: {hostname} (reason: {update_reason})")
        update_ingress_route(name, namespace, hostname, service_type, plural)
    else:
        logger.debug(f"{kind} {namespace}/{name} already correctly configured for {service_type}")

@kopf.on.event('traefik.io', 'v1alpha1', 'ingressroutes')
def on_ingressroute_event_traefik_io(name, namespace, body, **_):
    """Handle IngressRoute events for traefik.io API group."""
    handle_ingressroute_event(name, namespace, body, 'traefik.io', 'ingressroutes')

@kopf.on.event('traefik.containo.us', 'v1alpha1', 'ingressroutes')
def on_ingressroute_event_traefik_containo_us(name, namespace, body, **_):
    """Handle IngressRoute events for traefik.containo.us API group."""
    handle_ingressroute_event(name, namespace, body, 'traefik.containo.us', 'ingressroutes')

@kopf.on.event('traefik.io', 'v1alpha1', 'ingressroutetcps')
def on_ingressroutetcp_event_traefik_io(name, namespace, body, **_):
    """Handle IngressRouteTCP events for traefik.io API group."""
    handle_ingressroute_event(name, namespace, body, 'traefik.io', 'ingressroutetcps')

@kopf.on.event('traefik.containo.us', 'v1alpha1', 'ingressroutetcps')
def on_ingressroutetcp_event_traefik_containo_us(name, namespace, body, **_):
    """Handle IngressRouteTCP events for traefik.containo.us API group."""
    handle_ingressroute_event(name, namespace, body, 'traefik.containo.us', 'ingressroutetcps')

@kopf.on.event('traefik.io', 'v1alpha1', 'ingressrouteudps')
def on_ingressrouteudp_event_traefik_io(name, namespace, body, **_):
    """Handle IngressRouteUDP events for traefik.io API group."""
    handle_ingressroute_event(name, namespace, body, 'traefik.io', 'ingressrouteudps')

@kopf.on.event('traefik.containo.us', 'v1alpha1', 'ingressrouteudps')
def on_ingressrouteudp_event_traefik_containo_us(name, namespace, body, **_):
    """Handle IngressRouteUDP events for traefik.containo.us API group."""
    handle_ingressroute_event(name, namespace, body, 'traefik.containo.us', 'ingressrouteudps')

def watch_service():
    """Watch multiple services for changes in real-time."""
    global service_watch_active, service_hostnames
    
    if not service_configs:
        logger.error("No service configurations found, cannot watch services")
        return

    v1 = CoreV1Api()
    
    # Initialize service hostnames to avoid unnecessary sync on startup
    logger.info("Initializing current service hostnames...")
    for service_type, config in service_configs.items():
        try:
            svc = v1.read_namespaced_service(
                name=config['name'], 
                namespace=config['namespace']
            )
            if svc.status and svc.status.load_balancer and svc.status.load_balancer.ingress:
                hostname = format_lb_targets(svc.status.load_balancer.ingress)
                service_hostnames[service_type] = hostname
                logger.info(f"Initialized {service_type} service hostname: {hostname}")
            else:
                logger.info(f"No hostname available yet for {service_type} service")
        except Exception as e:
            logger.warning(f"Could not initialize hostname for {service_type} service: {str(e)}")
    
    logger.info(f"Starting watch for {len(service_configs)} services")
    
    try:
        service_watch_active = True
        logger.debug("Multi-service watch starting")
        
        # Watch each service in parallel using threads
        threads = []
        for service_type, config in service_configs.items():
            thread = threading.Thread(
                target=watch_single_service,
                args=(service_type, config, v1),
                daemon=True
            )
            thread.start()
            threads.append(thread)
            logger.debug(f"Started watch thread for {service_type} service")
        
        # Wait for all threads to complete (they should run indefinitely)
        for thread in threads:
            thread.join()
            
        logger.warning("All service watch threads have ended unexpectedly")
        
    except Exception as e:
        logger.error(f"Error in multi-service watch: {str(e)}")
        service_watch_active = False
        raise

def watch_single_service(service_type, config, v1_client):
    """Watch a single service for changes."""
    global service_hostnames
    
    ns = config['namespace']
    name = config['name']
    
    logger.info(f"Starting watch for {service_type} service: {ns}/{name}")
    
    while True:
        try:
            w = watch.Watch()
            
            for event in w.stream(
                v1_client.list_namespaced_service,
                namespace=ns,
                field_selector=f"metadata.name={name}",
                timeout_seconds=60
            ):
                event_type = event['type']
                svc = event['object']
                logger.debug(f"Service event received: {event_type} for {service_type} service {ns}/{name}")

                if svc.status and svc.status.load_balancer and svc.status.load_balancer.ingress:
                    hostname = format_lb_targets(svc.status.load_balancer.ingress)
                    current_hostname = service_hostnames.get(service_type)
                    
                    if hostname != current_hostname:
                        logger.info(f"Service {ns}/{name} ({service_type}) hostname changed from {current_hostname} to: {hostname}")
                        service_hostnames[service_type] = hostname
                        sync_all_ingress_routes(service_type, hostname)
                    else:
                        logger.debug(f"Service {ns}/{name} ({service_type}) hostname unchanged: {hostname}")
                else:
                    logger.debug(f"No ingress hostname available yet for {service_type} service {ns}/{name}")
                update_health()
                
        except Exception as e:
            logger.warning(f"Watch connection lost for {service_type} service {ns}/{name}: {str(e)}, reconnecting in 5 seconds")
            time.sleep(5)
            # Continue the while loop to reconnect

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global last_healthy_time, service_watch_active
        if self.path == '/healthz':
            current_time = time.time()
            if service_watch_active or (current_time - last_healthy_time) < 60:
                self.send_response(200)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(503)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Service Unhealthy")
                logger.warning(f"Health check failed: Service unhealthy (last healthy time: {time.ctime(last_healthy_time)})")
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_request(self, code='-', size='-'):
        """Override default logging to only log failures."""
        if str(code) == '503':
            self.log_message('"%s" %s %s', self.requestline, str(code), str(size))
        # Otherwise, do nothing (silence 200 OK)

def start_health_server():
    """Start the health check server in a separate thread."""
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    logger.info("Starting health check server on port 8080")
    server.serve_forever()

def sync_all_existing_ingress_routes():
    """Sync all existing IngressRoutes on startup to ensure all annotations are present."""
    logger.info("Starting initial sync of all existing Traefik routes...")
    api = CustomObjectsApi()
    total_synced = 0

    for group in active_api_groups:
        for plural in active_resources.get(group, []):
            try:
                ingress_routes = api.list_cluster_custom_object(
                    group=group,
                    version=TRAEFIK_VERSION,
                    plural=plural
                )

                for item in ingress_routes.get('items', []):
                    name = item['metadata']['name']
                    namespace = item['metadata']['namespace']

                    if not has_required_hostname(item, plural):
                        logger.debug(f"Initial sync: skipping {kind_for_plural(plural)} {namespace}/{name}: no {HOSTNAME_ANNOTATION} annotation")
                        continue

                    # Determine which service type this route should use
                    service_type = determine_service_type(item)
                    if not service_type:
                        continue

                    # Get hostname for the service type
                    hostname = get_lb_hostname(service_type)
                    if not hostname:
                        continue

                    # Check if cloudflare-proxied annotation is missing
                    annotations = item['metadata'].get('annotations', {})
                    cloudflare_proxied = annotations.get(CLOUDFLARE_PROXIED_ANNOTATION)
                    current_target = annotations.get(TARGET_ANNOTATION)
                    missing_proxied = plural in CLOUDFLARE_PROXIED_PLURALS and cloudflare_proxied is None

                    # Update if annotation is missing or hostname doesn't match
                    if missing_proxied or current_target != hostname:
                        logger.info(f"Initial sync: updating {kind_for_plural(plural)} {namespace}/{name} ({service_type})")
                        if update_ingress_route(name, namespace, hostname, service_type, plural):
                            total_synced += 1

            except Exception as e:
                logger.error(f"Error during initial sync of {plural} with API group {group}: {str(e)}")
                continue

    logger.info(f"Initial sync completed: {total_synced} Traefik routes updated")
    update_health()

@kopf.on.startup()
def start_service_watch(**_):
    logger.info("Starting service watch in background thread")
    watch_thread = threading.Thread(target=watch_service, daemon=True)
    watch_thread.start()
    
    logger.info("Starting health check server in background thread")
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Perform initial sync of all IngressRoutes in background
    logger.info("Starting initial sync in background thread")
    sync_thread = threading.Thread(target=sync_all_existing_ingress_routes, daemon=True)
    sync_thread.start()

def main():
    """Main entry point for the controller."""
    logger.info("Traefik External DNS Controller starting...")
    
    # Set environment variable to avoid user detection issues
    os.environ['KOPF_IDENTITY'] = 'traefik-external-dns-controller'
    
    # Run the kopf operator
    try:
        kopf.run(
            clusterwide=True,
            standalone=True
        )
    except KeyboardInterrupt:
        logger.info("Controller shutting down gracefully")
    except Exception as e:
        logger.error(f"Error running controller: {str(e)}")
        raise

if __name__ == "__main__":
    main()