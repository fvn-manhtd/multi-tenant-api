import os
import logging

from fastapi import FastAPI, Request, HTTPException # type: ignore
from pydantic import BaseModel, validator # type: ignore
from kubernetes import client, config # type: ignore
from kubernetes.client.rest import ApiException # type: ignore
from kubernetes.config.config_exception import ConfigException # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
import dns.resolver # type: ignore
import dns.update # type: ignore
import dns.query # type: ignore
from dotenv import load_dotenv # type: ignore
from sqlalchemy import create_engine, text # type: ignore

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

TENANTS = {}


# Define tenant model
class Tenant(BaseModel):
    name: str

def load_kubernetes_config():
    try:
        if os.getenv('KUBERNETES_SERVICE_HOST'):
            config.load_incluster_config()
        else:
            config.load_kube_config()
    except Exception as e:
        logger.error(f"Failed to load Kubernetes config: {e}")
        raise HTTPException(status_code=500, detail="Failed to load Kubernetes config")

def create_database(tenant_name: str):
    db_host = os.getenv('DB_HOST', 'localhost')
    db_user = os.getenv('DB_USER', 'postgres')
    db_password = os.getenv('DB_PASSWORD', 'password')
    db_port = os.getenv('DB_PORT', '5432')

    engine = create_engine(f'postgresql://{db_user}:{db_password}@{db_host}:{db_port}/postgres')

    with engine.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {tenant_name}_db"))


def create_yaml_files(tenant: Tenant):
    main_domain = os.getenv('MAIN_DOMAIN', 'central.local')
    domain = f"{tenant.name}.{main_domain}"
    api_domain = f"api.{tenant.name}.{main_domain}"

    backend_deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="backend"),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector=client.V1LabelSelector(
                match_labels={"app": "backend"}
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": "backend"}),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="backend",
                            image="backend-image",
                            ports=[client.V1ContainerPort(container_port=80)]
                        )
                    ]
                )
            )
        )
    )

    backend_service = client.V1Service(
        metadata=client.V1ObjectMeta(name="backend"),
        spec=client.V1ServiceSpec(
            selector={"app": "backend"},
            ports=[client.V1ServicePort(port=80, target_port=80)]
        )
    )

    frontend_deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="frontend"),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector=client.V1LabelSelector(
                match_labels={"app": "frontend"}
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": "frontend"}),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="frontend",
                            image="react-frontend",
                            image_pull_policy="Never", # this option is only pull the image from local
                            ports=[client.V1ContainerPort(container_port=80)]
                        )
                    ]
                )
            )
        )
    )

    frontend_service = client.V1Service(
        metadata=client.V1ObjectMeta(name="frontend"),
        spec=client.V1ServiceSpec(
            selector={"app": "frontend"},
            ports=[client.V1ServicePort(port=80, target_port=3000)]
        )
    )

    ingress = client.V1Ingress(
        metadata=client.V1ObjectMeta(
            name="tenant-ingress",
            namespace=tenant.name,
            annotations={
                "nginx.ingress.kubernetes.io/rewrite-target": "/",
            },
        ),
        spec=client.V1IngressSpec(
            rules=[
                client.V1IngressRule(
                    host=domain,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name="frontend",
                                        port=client.V1ServiceBackendPort(number=80)
                                    )
                                )
                            )
                        ]
                    )
                ),
                client.V1IngressRule(
                    host=api_domain,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name="backend",
                                        port=client.V1ServiceBackendPort(number=80)
                                    )
                                )
                            )
                        ]
                    )
                )
            ]
        )
    )

    return backend_deployment, backend_service, frontend_deployment, frontend_service, ingress

def apply_yaml_files(namespace: str, backend_deployment, backend_service, frontend_deployment, frontend_service, ingress):
    load_kubernetes_config()
    k8s_apps = client.AppsV1Api()
    k8s_core = client.CoreV1Api()
    k8s_networking = client.NetworkingV1Api()

    try:
        k8s_apps.create_namespaced_deployment(namespace, backend_deployment)
        k8s_core.create_namespaced_service(namespace, backend_service)
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Backend error: {e}")

    try:
        k8s_apps.create_namespaced_deployment(namespace, frontend_deployment)
        k8s_core.create_namespaced_service(namespace, frontend_service)
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Frontend error: {e}")

    try:
        k8s_networking.create_namespaced_ingress(namespace, ingress)
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Ingress error: {e}")

def update_dns_records(tenant: Tenant):
    main_domain = os.getenv('MAIN_DOMAIN', 'central.local')
    domain = f"{tenant.name}.{main_domain}"
    api_domain = f"api.{tenant.name}.{main_domain}"

    # Replace with your DNS server and zone details
    dns_server = "your-dns-server"
    dns_zone = "your-dns-zone"

    update = dns.update.Update(dns_zone)
    update.replace(domain, 300, 'A', 'your-ingress-controller-ip')
    update.replace(api_domain, 300, 'A', 'your-ingress-controller-ip')

    response = dns.query.tcp(update, dns_server)
    logger.info(f"DNS update response: {response}")

@app.post("/create-tenant/")
async def create_tenant(tenant: Tenant):
    namespace = tenant.name

    # Load Kubernetes configuration
    load_kubernetes_config()

    # Create instances of the Kubernetes API clients
    k8s_core = client.CoreV1Api()

    # Check if the namespace already exists
    try:
        k8s_core.read_namespace(name=namespace)
        raise HTTPException(status_code=400, detail=f"Namespace {namespace} already exists")
    except ApiException as e:
        if e.status != 404:
            raise HTTPException(status_code=500, detail=f"Failed to check namespace: {str(e)}")

    # Create the namespace
    namespace_body = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
    try:
        k8s_core.create_namespace(body=namespace_body)
    except ApiException as e:
        if e.status != 409:  # Ignore if namespace already exists
            raise HTTPException(status_code=500, detail=f"Namespace error: {e}")

    # Create the database for the tenant
    # create_database(tenant.name)

    # Create YAML files for the tenant's resources
    backend_deployment, backend_service, frontend_deployment, frontend_service, ingress = create_yaml_files(tenant)

    # Apply the generated YAML files
    apply_yaml_files(namespace, backend_deployment, backend_service, frontend_deployment, frontend_service, ingress)

    # Update DNS records only if not in development environment
    if os.getenv('ENVIRONMENT') != 'development':
        update_dns_records(tenant)

    return {"message": f"Tenant {tenant.name} created successfully"}
@app.get("/list-namespaces")
async def list_namespaces():
    try:
        # Load Kubernetes configuration
        load_kubernetes_config()

        # Create an instance of the CoreV1Api
        v1 = client.CoreV1Api()

        # List namespaces
        namespaces = v1.list_namespace()
        namespace_list = []
        for namespace in namespaces.items:
            namespace_name = namespace.metadata.name
            namespace_info = {
                "name": namespace_name,
                "labels": namespace.metadata.labels
            }
            namespace_list.append(namespace_info)
        
        return {"namespaces": namespace_list}
    
    except ApiException as e:
        logger.error(f"Exception when listing namespaces: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list namespaces: {str(e)}")

@app.delete("/remove-namespace/{namespace}")
async def remove_namespace(namespace: str):
    try:
        # Load Kubernetes configuration
        load_kubernetes_config()

        # Create an instance of the CoreV1Api
        v1 = client.CoreV1Api()

        # Delete the specified namespace
        v1.delete_namespace(name=namespace)
        logger.debug(f"Namespace {namespace} deleted successfully")

        # Remove tenant details from memory if it exists
        if namespace in TENANTS:
            del TENANTS[namespace]

        return {"message": f"Namespace {namespace} deleted successfully"}
    
    except ApiException as e:
        logger.error(f"Exception when deleting namespace: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete namespace: {str(e)}")


########################################################################################
# Define the allowed origins
origins = [
    "http://tenant1.central.local:3000",
    "http://api.tenant1.central.local",
    "http://localhost:3000"
    # Add other allowed origins here
]

# Add CORS middleware to the FastAPI application
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allow specific origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)


@app.get("/")
async def read_root(request: Request):    
    return {"message": "hello world"}