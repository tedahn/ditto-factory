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
    {
      name: "spawn_subagent",
      description:
        "Spawn a specialized child agent to handle a subtask. The child agent runs in its own container with its own Claude Code instance. Use this for independent subtasks that can run in parallel (e.g., 'write tests for module X while I implement module Y'). The child works on the same branch. Returns the child's result when complete.",
      inputSchema: {
        type: "object",
        properties: {
          task: {
            type: "string",
            description: "The task description for the child agent",
          },
          agent_type: {
            type: "string",
            description:
              "Optional agent type hint (e.g., 'frontend', 'backend'). If omitted, the controller auto-selects.",
            default: "",
          },
        },
        required: ["task"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (request.params.name === "check_messages") {
    const key = `queue:${THREAD_ID}`;
    const messages = await redis.lRange(key, 0, -1);
    await redis.del(key);

    if (!messages || messages.length === 0) {
      return { content: [{ type: "text", text: "No new messages" }] };
    }

    return {
      content: [{ type: "text", text: JSON.stringify(messages) }],
    };
  }

  if (request.params.name === "spawn_subagent") {
    const { task, agent_type } = request.params.arguments;
    const requestId = `${THREAD_ID}-sub-${Date.now()}`;

    // Check depth limit (parent passes SUBAGENT_DEPTH env var)
    const currentDepth = parseInt(process.env.SUBAGENT_DEPTH || "0");
    if (currentDepth >= 1) {
      return {
        content: [
          {
            type: "text",
            text: "ERROR: Subagent depth limit reached. Subagents cannot spawn sub-subagents.",
          },
        ],
      };
    }

    // Check count limit
    const countKey = `subagent_count:${THREAD_ID}`;
    const currentCount = parseInt((await redis.get(countKey)) || "0");
    if (currentCount >= 3) {
      return {
        content: [
          {
            type: "text",
            text: "ERROR: Maximum subagent limit (3) reached for this task.",
          },
        ],
      };
    }

    // Publish spawn request to Redis
    await redis.set(
      `subagent_request:${requestId}`,
      JSON.stringify({
        parent_thread_id: THREAD_ID,
        task: task,
        agent_type_hint: agent_type || "",
        request_id: requestId,
        timestamp: new Date().toISOString(),
      }),
      { EX: 700 }
    );

    // Increment count
    await redis.incr(countKey);
    await redis.expire(countKey, 3600);

    // Notify controller via pubsub
    await redis.publish("subagent_requests", requestId);

    // Poll for result (with timeout)
    const timeout = 600; // seconds
    const start = Date.now();
    while ((Date.now() - start) / 1000 < timeout) {
      const result = await redis.get(`subagent_result:${requestId}`);
      if (result) {
        await redis.del(`subagent_result:${requestId}`);
        const parsed = JSON.parse(result);
        return {
          content: [
            {
              type: "text",
              text: `Subagent completed (exit_code: ${parsed.exit_code}, commits: ${parsed.commit_count}):\n\nBranch: ${parsed.branch}\n${parsed.stderr || ""}`,
            },
          ],
        };
      }
      await new Promise((resolve) => setTimeout(resolve, 5000)); // poll every 5s
    }

    return {
      content: [
        {
          type: "text",
          text: "ERROR: Subagent timed out after 10 minutes.",
        },
      ],
    };
  }

  throw new Error(`Unknown tool: ${request.params.name}`);
});

const transport = new StdioServerTransport();
await server.connect(transport);

process.on("SIGINT", async () => {
  await redis.quit();
  process.exit(0);
});
