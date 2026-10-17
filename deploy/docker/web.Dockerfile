# Web 前端镜像。构建上下文必须是仓库根目录（compose 里 context: .），
# 这样 frontend/ 与 deploy/nginx/ 都在同一上下文内，COPY 不再越界。
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
