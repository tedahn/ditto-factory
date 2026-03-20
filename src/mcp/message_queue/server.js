import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { createClient } from "redis";

const THREAD_ID = process.env.THREAD_ID;
const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";

if (!THREAD_ID) {
  console.error("THREAD_ID environment variable is required");
  process.exit(1);
}

const redis = createClient({ url: REDIS_URL });
await redis.connect();

const server = new Server(
  { name: "df-message-queue", version: "0.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "check_messages",
      description:
        "Check for new follow-up messages from the user. Call this periodically during long tasks to see if the user has sent additional instructions.",
      inputSchema: { type: "object", properties: {} },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (request.params.name !== "check_messages") {
    throw new Error(`Unknown tool: ${request.params.name}`);
  }

  const key = `queue:${THREAD_ID}`;
  const messages = await redis.lRange(key, 0, -1);
  await redis.del(key);

  if (!messages || messages.length === 0) {
    return { content: [{ type: "text", text: "No new messages" }] };
  }

  return {
    content: [{ type: "text", text: JSON.stringify(messages) }],
  };
});

const transport = new StdioServerTransport();
await server.connect(transport);

process.on("SIGINT", async () => {
  await redis.quit();
  process.exit(0);
});
