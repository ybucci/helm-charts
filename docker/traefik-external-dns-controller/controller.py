import os
import logging
import warnings
import kopf
import kubernetes.config
from kubernetes.client import ApiClient, CustomObjectsApi, CoreV1Api
from kubernetes import watch
import threading
import time
import json
from queue import Queue
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# 3. Silence other loggers
for lib in ['kubernetes', 'urllib3', 'kopf', 'asyncio']:
    logging.getLogger(lib).handlers = []
    logging.getLogger(lib).propagate = False
    logging.getLogger(lib).setLevel(logging.WARNING)

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

def parse_service_config():
    """Parse service configuration from environment variables."""
    configs = {}
    
    # Parse EXTERNAL_SERVICE_REF (e.g., "traefik/traefik-external")
    external_ref = os.getenv('EXTERNAL_SERVICE_REF', '')
    if external_ref:
        try:
            ns, name = external_ref.split('/')
            configs['external'] = {'namespace': ns, 'name': name}
            logger.info(f"External LoadBalancer configured: {external_ref}")
        except ValueError:
            logger.error(f"Invalid EXTERNAL_SERVICE_REF format: {external_ref}")
    
    # Parse INTERNAL_SERVICE_REF (e.g., "traefik/traefik-internal")
    internal_ref = os.getenv('INTERNAL_SERVICE_REF', '')
    if internal_ref:
        try:
            ns, name = internal_ref.split('/')
            configs['internal'] = {'namespace': ns, 'name': name}
            logger.info(f"Internal LoadBalancer configured: {internal_ref}")
        except ValueError:
            logger.error(f"Invalid INTERNAL_SERVICE_REF format: {internal_ref}")
    
    # Fallback to legacy MONITOR_SERVICE_REF as external
    if not configs:
        monitor_ref = os.getenv('MONITOR_SERVICE_REF')
        if monitor_ref:
            try:
                ns, name = monitor_ref.split('/')
                configs['external'] = {'namespace': ns, 'name': name}
                logger.info(f"Using legacy MONITOR_SERVICE_REF as external: {ns}/{name}")
            except ValueError:
                logger.error(f"Invalid MONITOR_SERVICE_REF format: {monitor_ref}")
    
    return configs

@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    global service_configs
    
    settings.persistence.finalizer = None
    settings.watching.clusterwide = True
    settings.posting.enabled = False
    settings.watching.server_timeout = 60
    settings.watching.reconnect_backoff = 1.0
    
    # Parse service configurations
    service_configs = parse_service_config()
    
    if not service_configs:
        logger.error("No service configurations found! Set EXTERNAL_SERVICE_REF and/or INTERNAL_SERVICE_REF")
        return
    
    logger.info("Controller started successfully | Monitoring services: %s", 
                ", ".join([f"{k}={v['namespace']}/{v['name']}" for k, v in service_configs.items()]))
    
    try:
        kubernetes.config.load_incluster_config()
        logger.info("In-cluster configuration loaded")
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
        logger.info("Local configuration (kubeconfig) loaded")
    update_health()

def get_lb_hostname(service_type):
    """Get hostname for a specific service type."""
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
            hostname = svc.status.load_balancer.ingress[0].hostname or svc.status.load_balancer.ingress[0].ip
            logger.debug(f"Load balancer hostname obtained for {service_type}: {hostname}")
            return hostname
    except Exception as e:
        logger.error(f"Error obtaining load balancer hostname for {service_type}: {str(e)}")
    return None

def determine_service_type(ingress_route):
    """Determine which service type to use for an IngressRoute."""
    annotations = ingress_route.get('metadata', {}).get('annotations', {})
    
    # Check for explicit annotation
    lb_type = annotations.get('traefik.io/load-balancer-type', '').lower()
    if lb_type in ['internal', 'external']:
        return lb_type
    
    # Check for internal annotation patterns
    if annotations.get('traefik.io/internal', '').lower() == 'true':
        return 'internal'
    
    # Check for external annotation patterns  
    if annotations.get('traefik.io/external', '').lower() == 'true':
        return 'external'
    
    # Default behavior based on available configurations
    if 'external' in service_configs:
        return 'external'
    elif 'internal' in service_configs:
        return 'internal'
    
    return None

def update_ingress_route(name, namespace, hostname, service_type):
    """Update IngressRoute with hostname and service type information."""
    try:
        api = CustomObjectsApi()
        current = api.get_namespaced_custom_object(
            group="traefik.io",
            version="v1alpha1",
            namespace=namespace,
            plural="ingressroutes",
            name=name
        )
        
        resource_key = f"{namespace}/{name}"
        current_time = time.time()
        if resource_key in last_updated and (current_time - last_updated[resource_key]) < 5:
            logger.debug(f"Ignoring redundant update for {resource_key}")
            return False
        
        # Build annotations
        annotations = current.get('metadata', {}).get('annotations', {})
        annotations['external-dns.alpha.kubernetes.io/target'] = hostname
        annotations['traefik.io/load-balancer-type'] = service_type
        
        patch = {
            'metadata': {
                'annotations': annotations
            }
        }
        
        response = api.patch_namespaced_custom_object(
            group="traefik.io",
            version="v1alpha1",
            namespace=namespace,
            plural="ingressroutes",
            name=name,
            body=patch
        )
        
        last_updated[resource_key] = current_time
        logger.info(f"IngressRoute {namespace}/{name} updated with {service_type} hostname: {hostname}")
        update_health()
        return True
    except Exception as e:
        logger.error(f"Failed to update IngressRoute {namespace}/{name}: {str(e)}")
        return False

def sync_all_ingress_routes(service_type, new_hostname):
    """Sync all IngressRoutes of a specific service type with the new hostname."""
    api = CustomObjectsApi()
    try:
        ingress_routes = api.list_cluster_custom_object(
            group="traefik.io",
            version="v1alpha1",
            plural="ingressroutes"
        )
        
        updated_count = 0
        for item in ingress_routes.get('items', []):
            name = item['metadata']['name']
            namespace = item['metadata']['namespace']
            
            # Determine if this IngressRoute should use this service type
            determined_type = determine_service_type(item)
            if determined_type != service_type:
                continue
                
            current_target = item['metadata'].get('annotations', {}).get('external-dns.alpha.kubernetes.io/target')
            if current_target != new_hostname:
                logger.info(f"Updating via sync IngressRoute {namespace}/{name} ({service_type}) from {current_target} to {new_hostname}")
                if update_ingress_route(name, namespace, new_hostname, service_type):
                    updated_count += 1
        
        logger.info(f"Synchronized {updated_count} IngressRoutes for {service_type} LoadBalancer")
    except Exception as e:
        logger.error(f"Failed to sync IngressRoutes for {service_type}: {str(e)}")
    update_health()

@kopf.on.event('traefik.io', 'v1alpha1', 'ingressroutes')
def on_ingressroute_event(name, namespace, body, **_):
    """Handle IngressRoute events."""
    logger.debug(f"Event received for IngressRoute: {namespace}/{name}")
    
    # Determine which service type this IngressRoute should use
    service_type = determine_service_type(body)
    if not service_type:
        logger.warning(f"Could not determine service type for IngressRoute {namespace}/{name}")
        return
    
    # Get hostname for the determined service type
    hostname = get_lb_hostname(service_type)
    if not hostname:
        logger.warning(f"No hostname available for {service_type} LoadBalancer for IngressRoute {namespace}/{name}")
        return
    
    # Check if update is needed
    current_target = body['metadata'].get('annotations', {}).get('external-dns.alpha.kubernetes.io/target')
    current_type = body['metadata'].get('annotations', {}).get('traefik.io/load-balancer-type')
    
    if current_target != hostname or current_type != service_type:
        logger.info(f"Updating via event IngressRoute {namespace}/{name} with {service_type} hostname: {hostname}")
        update_ingress_route(name, namespace, hostname, service_type)
    elif current_target is None:
        logger.info(f"Adding annotation to IngressRoute {namespace}/{name} with {service_type} hostname: {hostname}")
        update_ingress_route(name, namespace, hostname, service_type)

def watch_service():
    """Watch multiple services for changes in real-time."""
    global service_watch_active, service_hostnames
    
    if not service_configs:
        logger.error("No service configurations found, cannot watch services")
        return

    v1 = CoreV1Api()
    watches = {}
    
    # Initialize watches for each configured service
    for service_type, config in service_configs.items():
        watches[service_type] = watch.Watch()
        logger.info(f"Initializing watch for {service_type} service: {config['namespace']}/{config['name']}")

    logger.info(f"Starting watch for {len(service_configs)} services")
    
    while True:
        try:
            service_watch_active = True
            logger.debug("Multi-service watch loop active")
            
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
            
            # Wait for all threads to complete (they shouldn't under normal circumstances)
            for thread in threads:
                thread.join(timeout=1)
                
            logger.debug("Multi-service watch ended, restarting")
            time.sleep(5)
            
        except Exception as e:
            logger.error(f"Error in multi-service watch: {str(e)}, reconnecting in 5 seconds")
            service_watch_active = False
            time.sleep(5)

def watch_single_service(service_type, config, v1_client):
    """Watch a single service for changes."""
    global service_hostnames
    
    w = watch.Watch()
    ns = config['namespace']
    name = config['name']
    
    logger.info(f"Starting individual watch for {service_type} service: {ns}/{name}")
    
    while True:
        try:
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
                    hostname = svc.status.load_balancer.ingress[0].hostname or svc.status.load_balancer.ingress[0].ip
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
            logger.error(f"Error watching {service_type} service {ns}/{name}: {str(e)}, reconnecting in 5 seconds")
            time.sleep(5)
            break

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

@kopf.on.startup()
def start_service_watch(**_):
    logger.debug("Starting service watch in background thread")
    watch_thread = threading.Thread(target=watch_service, daemon=True)
    watch_thread.start()
    
    logger.debug("Starting health check server in background thread")
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start() 