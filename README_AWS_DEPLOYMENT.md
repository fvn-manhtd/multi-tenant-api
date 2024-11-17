# FastAPI Multi-Tenant API Deployment on AWS EKS

This repository contains a FastAPI application with multi-tenant support, deployed using Kubernetes on AWS EKS.

## Prerequisites

- AWS CLI
- eksctl
- kubectl
- Docker
- AWS ECR (Elastic Container Registry)
- Python 3.10 or later

## Steps to Deploy to AWS EKS

### 1. Set Up AWS EKS Cluster

You can create an EKS cluster using the AWS Management Console, AWS CLI, or eksctl. Here is an example using eksctl:

```sh
# Install eksctl if you haven't already
brew tap weaveworks/tap
brew install weaveworks/tap/eksctl

# Create an EKS cluster
eksctl create cluster --name my-cluster --region ap-northeast-1 --nodegroup-name my-nodes --node-type t3.medium --nodes 3
```


2. Configure kubectl
```sh
aws eks --region ap-northeast-1 update-kubeconfig --name my-cluster
```


3. Build and Push Docker Image
Build the Docker image for your FastAPI application and push it to Amazon ECR:
```sh
# Authenticate Docker to your Amazon ECR registry
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin <your-account-id>.dkr.ecr.us-west-2.amazonaws.com

# Build the Docker image
docker build -t my-fastapi-app .

# Tag the Docker image
docker tag my-fastapi-app:latest <your-account-id>.dkr.ecr.us-west-2.amazonaws.com/my-fastapi-app:latest

# Push the Docker image to Amazon ECR
docker push <your-account-id>.dkr.ecr.us-west-2.amazonaws.com/my-fastapi-app:latest
```

4. Create Kubernetes Manifests
Create Kubernetes manifests for your FastAPI application, including deployments, services, and ingress.


5. Apply Kubernetes Manifests
Apply the Kubernetes manifests to your EKS cluster:
```sh
kubectl apply -f _kube/central/backend-deployment.yaml
kubectl apply -f _kube/central/backend-service.yaml
kubectl apply -f ingress.yaml
```

6. Update DNS Records
Update your DNS records to point to the Ingress controller. You can use Route 53 or any other DNS provider to update the DNS records.
