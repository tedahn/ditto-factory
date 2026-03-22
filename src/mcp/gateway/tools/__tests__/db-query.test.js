import { queryDatabase } from "../db-query.js";

test("rejects non-SELECT queries", async () => {
  const result = await queryDatabase({ query: "DROP TABLE users" });
  expect(result.text).toContain("ERROR: Only SELECT");
});

test("rejects multi-statement injection", async () => {
  const result = await queryDatabase({ query: "SELECT 1; DROP TABLE users" });
  expect(result.text).toContain("ERROR: Multi-statement");
});

test("returns error when DATABASE_URL not set", async () => {
  delete process.env.GATEWAY_DATABASE_URL;
  const result = await queryDatabase({ query: "SELECT 1" });
  expect(result.text).toContain("ERROR");
});
