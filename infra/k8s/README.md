# Local Kubernetes Setup

## Prerequisites
- Docker Desktop
- kubectl
- kind
- GNU Make
- A running kind cluster named `timecard-local`

## 1. Create the namespace
```powershell
kubectl create namespace dayforce-local
```

## 2. Create the PostgreSQL Secret
```powershell
$dbPassword = [guid]::NewGuid().ToString("N")
kubectl -n dayforce-local create secret generic postgres-secret --from-literal="POSTGRES_PASSWORD=$dbPassword"
Remove-Variable dbPassword
```

Do not commit database passwords or Secret manifests containing credentials.

## 3. Build and load the frontend image
```powershell
docker build -t docker-frontend:latest ./frontend
kind load docker-image docker-frontend:latest --name timecard-local
```

## 4. Deploy
```powershell
make k8s-up
```

## 5. Access the frontend
```powershell
kubectl port-forward -n dayforce-local service/frontend 3000:8080
```

Open http://localhost:3000.

## 6. Stop workloads
```powershell
make k8s-down
```

This scales deployments to zero and preserves the PostgreSQL PVC.
Restart with `make k8s-up`.

## Deployed resources
- PostgreSQL 16 with pgvector
- PostgreSQL ConfigMaps and initialization SQL
- PersistentVolumeClaim (1 GiB)
- Frontend Deployment and Service
- Readiness and liveness probes
- CPU and memory requests and limits

The backend Deployment is not included because its application entry point is not yet available.
