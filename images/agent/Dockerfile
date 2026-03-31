FROM node:22-slim

RUN apt-get update && apt-get install -y \
    git build-essential python3 python3-pip curl jq redis-tools \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# Create workspace directory before switching to non-root user
RUN mkdir -p /workspace && chown 1000:1000 /workspace

# node:22-slim already has user 1000 (node)
WORKDIR /home/node

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY mcp.json /etc/df/mcp.json

RUN chmod +x /usr/local/bin/entrypoint.sh
USER 1000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
