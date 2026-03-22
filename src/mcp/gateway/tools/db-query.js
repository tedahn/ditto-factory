/**
 * Read-only database query tool.
 * Uses pg (PostgreSQL) with enforced read-only mode.
 * Requires GATEWAY_DATABASE_URL env var.
 */
import pg from "pg";

let pool = null;

function getPool() {
  if (!pool) {
    const dbUrl = process.env.GATEWAY_DATABASE_URL;
    if (!dbUrl) {
      throw new Error("GATEWAY_DATABASE_URL not configured");
    }
    pool = new pg.Pool({
      connectionString: dbUrl,
      max: 5,
      statement_timeout: 10000, // 10s max query time
    });
  }
  return pool;
}

export async function queryDatabase({ query, database }) {
  // Validate: only SELECT statements allowed
  const trimmed = query.trim();
  const upperFirst = trimmed.split(/\s+/)[0].toUpperCase();

  const allowedStatements = ["SELECT", "WITH", "EXPLAIN"];
  if (!allowedStatements.includes(upperFirst)) {
    return {
      type: "text",
      text: `ERROR: Only SELECT, WITH, and EXPLAIN queries are allowed. Got: ${upperFirst}`,
    };
  }

  // Block dangerous patterns
  const dangerous = /;\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)/i;
  if (dangerous.test(trimmed)) {
    return { type: "text", text: "ERROR: Multi-statement or write operations are not allowed." };
  }

  try {
    const p = getPool();
    const client = await p.connect();

    try {
      // Set session to read-only
      await client.query("SET TRANSACTION READ ONLY");

      // Add LIMIT if not present (prevent massive result sets)
      let safeQuery = trimmed;
      if (!safeQuery.match(/LIMIT\s+\d+/i) && upperFirst === "SELECT") {
        safeQuery = safeQuery.replace(/;?\s*$/, " LIMIT 100");
      }

      const result = await client.query(safeQuery);

      const output = {
        rows: result.rows.slice(0, 100),
        row_count: result.rowCount,
        fields: result.fields.map(f => f.name),
      };

      return { type: "text", text: JSON.stringify(output, null, 2) };
    } finally {
      client.release();
    }
  } catch (err) {
    return { type: "text", text: `ERROR: Query failed: ${err.message}` };
  }
}

export const definition = {
  name: "query_database",
  description: "Run a read-only SQL query (SELECT/WITH/EXPLAIN only). Results limited to 100 rows. 10-second timeout.",
  inputSchema: {
    type: "object",
    properties: {
      query: { type: "string", description: "SQL query (SELECT only)" },
      database: { type: "string", description: "Database identifier (reserved for future multi-db support)" },
    },
    required: ["query"],
  },
};
