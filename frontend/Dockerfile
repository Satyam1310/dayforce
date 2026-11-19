FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY . .

ARG VITE_USE_MOCK=false
ENV VITE_USE_MOCK=${VITE_USE_MOCK}

RUN npm run build


FROM nginxinc/nginx-unprivileged:alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
