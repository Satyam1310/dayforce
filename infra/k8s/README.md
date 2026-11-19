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
docker build -t dayforce-frontend:step1 --build-arg VITE_USE_MOCK=false ./frontend
kind load docker-image dayforce-frontend:step1 --name timecard-local
```

## 4. Build and load the backend image
```powershell
docker build -t dayforce-backend:step1 -f backend/Dockerfile .
kind load docker-image dayforce-backend:step1 --name timecard-local
```

## 5. Deploy
```powershell
make k8s-up
```

This deploys PostgreSQL, the backend, and the frontend. The backend uses the `backend-config` ConfigMap and the locally created `postgres-secret`.

## 6. Access the frontend
```powershell
kubectl port-forward -n dayforce-local service/frontend 3000:8080
```

Open http://localhost:3000.

## 7. Check backend health
```powershell
kubectl port-forward -n dayforce-local service/backend 8000:8000
```

In another terminal:
```powershell
Invoke-RestMethod http://localhost:8000/health
```

## 8. Stop workloads
```powershell
make k8s-down
```

This scales the frontend and PostgreSQL deployments to zero and preserves the PostgreSQL PVC. Restart with `make k8s-up`.

## Deployed resources
- PostgreSQL 16 with pgvector
- PostgreSQL ConfigMaps and initialization SQL
- PersistentVolumeClaim (1 GiB)
- Backend Deployment (2 initial replicas) and ClusterIP Service
- Backend ConfigMap
- Backend readiness and liveness probes on `/health`
- Backend CPU and memory requests and limits
- Backend HorizontalPodAutoscaler (CPU target 70%, minimum 2 and maximum 6 replicas)
- Frontend Deployment and Service
- Frontend Nginx `/api/` reverse proxy to the backend Service
- Frontend readiness and liveness probes
- Pinned frontend and backend image tags

## HPA metrics
The HorizontalPodAutoscaler requires a working Metrics Server. For a local kind cluster, install it with:
```powershell
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

If kind kubelet certificate validation prevents metrics collection, a local-only workaround is to configure Metrics Server with `--kubelet-insecure-tls`. Do not use this workaround in production.
