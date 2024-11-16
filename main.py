import os
import base64
import logging
import subprocess
from typing import Union

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

TENANTS = {}

# Define tenant model
class Tenant(BaseModel):
    name: str
    domain: str
    api_domain: str

def load_kubernetes_config():
    try:
        if os.getenv('KUBERNETES_SERVICE_HOST'):
            # Running inside a Kubernetes cluster
            logger.debug("Loading Kubernetes configuration from within the cluster")
            config.load_incluster_config()
        else:
            # Running outside a Kubernetes cluster
            kubeconfig_path = os.getenv('KUBECONFIG', '~/.kube/config')
            logger.debug(f"KUBECONFIG environment variable: {kubeconfig_path}")

            # Check if the kube-config file exists and log its contents
            kubeconfig_path = os.path.expanduser(kubeconfig_path)
            if os.path.exists(kubeconfig_path):
                with open(kubeconfig_path, 'r') as f:
                    logger.debug(f"Kube-config file contents:\n{f.read()}")
            else:
                logger.error(f"Kube-config file not found at: {kubeconfig_path}")

            # Load Kubernetes configuration from kubeconfig file
            logger.debug("Loading Kubernetes configuration from kubeconfig file")
            config.load_kube_config(config_file=kubeconfig_path)
        
        logger.debug("Kubernetes configuration loaded successfully")
    except ConfigException as e:
        logger.error(f"Failed to load Kubernetes config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load Kubernetes config: {str(e)}")

def create_yaml_files(tenant: Tenant):
    namespace = tenant.name

    backend_deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  namespace: {namespace}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      containers:
      - name: backend
        image: fastapi-backend
        ports:
        - containerPort: 8000
"""

    backend_service_yaml = f"""
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: {namespace}
spec:
  selector:
    app: backend
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
"""

    frontend_deployment_yaml = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  namespace: {namespace}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: frontend
  template:
    metadata:
      labels:
        app: frontend
    spec:
      containers:
      - name: frontend
        image: react-frontend
        ports:
        - containerPort: 3000
"""

    frontend_service_yaml = f"""
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: {namespace}
spec:
  selector:
    app: frontend
  ports:
  - protocol: TCP
    port: 80
    targetPort: 3000
"""

    ingress_yaml = f"""
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tenant-ingress
  namespace: {namespace}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  rules:
  - host: {tenant.domain}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: frontend
            port:
              number: 80
  - host: {tenant.api_domain}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: backend
            port:
              number: 80
"""

    os.makedirs(f"./_kube/{namespace}", exist_ok=True)
    with open(f"./_kube/{namespace}/backend-deployment.yaml", "w") as f:
        f.write(backend_deployment_yaml)
    with open(f"./_kube/{namespace}/backend-service.yaml", "w") as f:
        f.write(backend_service_yaml)
    with open(f"./_kube/{namespace}/frontend-deployment.yaml", "w") as f:
        f.write(frontend_deployment_yaml)
    with open(f"./_kube/{namespace}/frontend-service.yaml", "w") as f:
        f.write(frontend_service_yaml)
    with open(f"./_kube/{namespace}/ingress.yaml", "w") as f:
        f.write(ingress_yaml)

def apply_yaml_files(namespace: str):
    try:
        subprocess.run(["kubectl", "apply", "-f", f"./_kube/{namespace}/backend-deployment.yaml"], check=True)
        subprocess.run(["kubectl", "apply", "-f", f"./_kube/{namespace}/backend-service.yaml"], check=True)
        subprocess.run(["kubectl", "apply", "-f", f"./_kube/{namespace}/frontend-deployment.yaml"], check=True)
        subprocess.run(["kubectl", "apply", "-f", f"./_kube/{namespace}/frontend-service.yaml"], check=True)
        subprocess.run(["kubectl", "apply", "-f", f"./_kube/{namespace}/ingress.yaml"], check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Error applying YAML files: {e}")

@app.post("/create-tenant/")
async def create_tenant(tenant: Tenant):
    namespace = tenant.name

    # Load Kubernetes configuration
    load_kubernetes_config()

    # Create instances of the Kubernetes API clients
    k8s_core = client.CoreV1Api()
    k8s_apps = client.AppsV1Api()
    k8s_networking = client.NetworkingV1Api()

    # Check if the api_domain already exists in the namespace
    try:
        ingresses = k8s_networking.list_namespaced_ingress(namespace=namespace)
        for ingress in ingresses.items:
            for rule in ingress.spec.rules:
                if rule.host == tenant.api_domain:
                    raise HTTPException(status_code=400, detail=f"API domain {tenant.api_domain} already exists in namespace {namespace}")
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Error checking ingresses: {e}")

    # Step 1: Create Namespace
    try:
        ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
        k8s_core.create_namespace(ns)
    except ApiException as e:
        if e.status != 409:  # Ignore if namespace already exists
            raise HTTPException(status_code=500, detail=f"Namespace error: {e}")

    # Step 2: Create Backend Deployment and Service
    backend_deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="backend", namespace=namespace),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector={"matchLabels": {"app": "backend"}},
            template=client.V1PodTemplateSpec(
                metadata={"labels": {"app": "backend"}},
                spec=client.V1PodSpec(containers=[
                    client.V1Container(
                        name="backend",
                        image="your-docker-repo/fastapi-backend:latest",
                        ports=[client.V1ContainerPort(container_port=8000)],
                    )
                ]),
            ),
        ),
    )
    backend_service = client.V1Service(
        metadata=client.V1ObjectMeta(name="backend", namespace=namespace),
        spec=client.V1ServiceSpec(
            selector={"app": "backend"},
            ports=[client.V1ServicePort(protocol="TCP", port=80, target_port=8000)],
        ),
    )

    try:
        k8s_apps.create_namespaced_deployment(namespace, backend_deployment)
        k8s_core.create_namespaced_service(namespace, backend_service)
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Backend error: {e}")

    # Step 3: Create Frontend Deployment and Service
    frontend_deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="frontend", namespace=namespace),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector={"matchLabels": {"app": "frontend"}},
            template=client.V1PodTemplateSpec(
                metadata={"labels": {"app": "frontend"}},
                spec=client.V1PodSpec(containers=[
                    client.V1Container(
                        name="frontend",
                        image="your-docker-repo/react-frontend:latest",
                        ports=[client.V1ContainerPort(container_port=3000)],
                    )
                ]),
            ),
        ),
    )
    frontend_service = client.V1Service(
        metadata=client.V1ObjectMeta(name="frontend", namespace=namespace),
        spec=client.V1ServiceSpec(
            selector={"app": "frontend"},
            ports=[client.V1ServicePort(protocol="TCP", port=80, target_port=3000)],
        ),
    )

    try:
        k8s_apps.create_namespaced_deployment(namespace, frontend_deployment)
        k8s_core.create_namespaced_service(namespace, frontend_service)
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Frontend error: {e}")

    # Step 4: Create Ingress for Frontend and Backend
    ingress = client.V1Ingress(
        metadata=client.V1ObjectMeta(
            name="tenant-ingress",
            namespace=namespace,
            annotations={
                "nginx.ingress.kubernetes.io/rewrite-target": "/",
            },
        ),
        spec=client.V1IngressSpec(
            rules=[
                client.V1IngressRule(
                    host=tenant.domain,
                    http=client.V1HTTPIngressRuleValue(paths=[
                        client.V1HTTPIngressPath(
                            path="/",
                            path_type="Prefix",
                            backend=client.V1IngressBackend(
                                service=client.V1IngressServiceBackend(
                                    name="frontend",
                                    port=client.V1ServiceBackendPort(number=80),
                                )
                            ),
                        )
                    ]),
                ),
                client.V1IngressRule(
                    host=tenant.api_domain,
                    http=client.V1HTTPIngressRuleValue(paths=[
                        client.V1HTTPIngressPath(
                            path="/",
                            path_type="Prefix",
                            backend=client.V1IngressBackend(
                                service=client.V1IngressServiceBackend(
                                    name="backend",
                                    port=client.V1ServiceBackendPort(number=80),
                                )
                            ),
                        )
                    ]),
                ),
            ]
        ),
    )

    try:
        k8s_networking.create_namespaced_ingress(namespace, ingress)
    except ApiException as e:
        raise HTTPException(status_code=500, detail=f"Ingress error: {e}")

    return {"message": f"Tenant '{tenant.name}' successfully created with domains {tenant.domain} and {tenant.api_domain}."}

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
            domain = f"{namespace_name}.central.local"
            api_domain = f"api.{namespace_name}.central.local"
            namespace_info = {
                "name": namespace_name,
                "labels": namespace.metadata.labels,
                "domain": domain,
                "api_domain": api_domain
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