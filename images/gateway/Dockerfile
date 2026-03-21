FROM node:22-slim

WORKDIR /app

COPY src/mcp/gateway/package.json ./
RUN npm install --production

COPY src/mcp/gateway/ ./

RUN useradd -m -u 1000 gateway
USER 1000

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD node -e "fetch('http://localhost:3001/health').then(r => r.ok ? process.exit(0) : process.exit(1))"

ENTRYPOINT ["node", "server.js"]
