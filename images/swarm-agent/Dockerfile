# Swarm agent image with df-swarm-comms MCP server
# The MCP server runs alongside Claude Code as a stdio-based sidecar

FROM node:20-alpine AS mcp-build

WORKDIR /mcp
COPY src/mcp/swarm_comms/package.json .
RUN npm install --production
COPY src/mcp/swarm_comms/ .

FROM ubuntu:22.04

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    jq \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy MCP server from build stage
COPY --from=mcp-build /mcp /opt/df-swarm-comms

# Create non-root user
RUN useradd -m -u 1000 agent
USER 1000
WORKDIR /workspace

# The entry point is configured by the K8s Job spec
# MCP server is started via the agent's mcp.json configuration
