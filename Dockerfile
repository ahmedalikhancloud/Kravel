FROM node:24-alpine

WORKDIR /app
COPY package.json ./
COPY src ./src

RUN mkdir -p /data && chown -R node:node /app /data
USER node

ENV KRAVEL_DB_PATH=/data/kravel.db \
    KRAVEL_HOST=0.0.0.0 \
    KRAVEL_PORT=8080
EXPOSE 8080

CMD ["node", "src/main.mjs", "serve-watch"]
