FROM node:22-slim

RUN apt-get update && apt-get install -y \
    git build-essential python3 python3-pip curl jq redis-tools \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN useradd -m -u 1000 agent
WORKDIR /home/agent

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY mcp.json /etc/aal/mcp.json

RUN chmod +x /usr/local/bin/entrypoint.sh
USER 1000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
