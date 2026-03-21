import express from "express";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { createClient } from "redis";
import { randomUUID } from "crypto";

const PORT = parseInt(process.env.GATEWAY_PORT || "3001", 10);
const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

// ---------------------------------------------------------------------------
// Redis connection
// ---------------------------------------------------------------------------
const redis = createClient({ url: REDIS_URL });
redis.on("error", (err) => console.error("Redis error:", err));
await redis.connect();
console.log("Connected to Redis at", REDIS_URL);

// ---------------------------------------------------------------------------
// Tool registry — all tools the gateway can expose.
// Handlers are placeholders; real backends will be plugged in later.
// ---------------------------------------------------------------------------
const TOOL_REGISTRY = {
  "file-analysis": {
    name: "analyze_file",
    description:
      "Analyze a file's structure, dependencies, and quality metrics",
    inputSchema: {
      type: "object",
      properties: {
        file_path: { type: "string", description: "Path to file to analyze" },
        analysis_type: {
          type: "string",
          enum: ["structure", "dependencies", "quality", "all"],
          default: "all",
        },
      },
      required: ["file_path"],
    },
    handler: async (args) => ({
      type: "text",
      text: `Analysis of ${args.file_path}: [placeholder - implement with actual tool backend]`,
    }),
  },

  "web-search": {
    name: "search_web",
    description:
      "Search the web for information relevant to the current task",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        max_results: { type: "number", default: 5 },
      },
      required: ["query"],
    },
    handler: async (args) => ({
      type: "text",
      text: `Search results for "${args.query}": [placeholder]`,
    }),
  },

  "db-query": {
    name: "query_database",
    description: "Run a read-only SQL query against a database",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "SQL query (SELECT only)",
        },
        database: { type: "string", description: "Database identifier" },
      },
      required: ["query"],
    },
    handler: async (args) => {
      if (!args.query.trim().toUpperCase().startsWith("SELECT")) {
        return {
          type: "text",
          text: "ERROR: Only SELECT queries are allowed",
        };
      }
      return {
        type: "text",
        text: "Query result: [placeholder - connect to actual database]",
      };
    },
  },
};

// ---------------------------------------------------------------------------
// Express application
// ---------------------------------------------------------------------------
const app = express();
app.use(express.json());

// Health check
app.get("/health", (_req, res) => {
  res.json({ status: "ok", tools: Object.keys(TOOL_REGISTRY).length });
});

// Track active transports keyed by session id so /messages can route correctly
const activeSessions = new Map();

// ---------------------------------------------------------------------------
// SSE endpoint — creates a per-session MCP server scoped to allowed tools
// ---------------------------------------------------------------------------
app.get("/sse", async (req, res) => {
  const threadId = req.query.thread_id;
  if (!threadId) {
    return res.status(400).json({ error: "thread_id query parameter is required" });
  }

  // Resolve allowed tools for this session from Redis
  const scopeRaw = await redis.get(`gateway_scope:${threadId}`);
  const allowedTools = scopeRaw
    ? JSON.parse(scopeRaw)
    : Object.keys(TOOL_REGISTRY);

  const sessionTools = {};
  for (const toolKey of allowedTools) {
    if (TOOL_REGISTRY[toolKey]) {
      sessionTools[toolKey] = TOOL_REGISTRY[toolKey];
    }
  }

  console.log(
    `[${threadId}] SSE session started — exposing tools: ${Object.keys(sessionTools).join(", ") || "(none)"}`,
  );

  // Build per-session MCP server
  const server = new Server(
    { name: "df-gateway", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: Object.values(sessionTools).map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
    })),
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const toolKey = Object.keys(sessionTools).find(
      (k) => sessionTools[k].name === request.params.name,
    );
    if (!toolKey) {
      throw new Error(
        `Tool "${request.params.name}" is not available for this session`,
      );
    }
    const result = await sessionTools[toolKey].handler(
      request.params.arguments || {},
    );
    return { content: [result] };
  });

  // Create SSE transport with a unique session path
  const sessionId = randomUUID();
  const sessionPath = `/messages/${sessionId}`;
  const transport = new SSEServerTransport(sessionPath, res);

  activeSessions.set(sessionId, transport);

  // Clean up on disconnect
  res.on("close", () => {
    activeSessions.delete(sessionId);
    console.log(`[${threadId}] SSE session ${sessionId} closed`);
  });

  await server.connect(transport);
});

// ---------------------------------------------------------------------------
// Messages endpoint — routes incoming JSON-RPC to the correct session
// ---------------------------------------------------------------------------
app.post("/messages/:sessionId", async (req, res) => {
  const transport = activeSessions.get(req.params.sessionId);
  if (!transport) {
    return res.status(404).json({ error: "Session not found" });
  }
  await transport.handlePostMessage(req, res);
});

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------
app.listen(PORT, () => {
  console.log(`MCP Gateway listening on port ${PORT}`);
  console.log(
    `Available tools: ${Object.keys(TOOL_REGISTRY).join(", ")}`,
  );
});

// Graceful shutdown
process.on("SIGINT", async () => {
  console.log("Shutting down MCP Gateway...");
  await redis.quit();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  console.log("Shutting down MCP Gateway...");
  await redis.quit();
  process.exit(0);
});
