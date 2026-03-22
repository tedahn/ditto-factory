import { searchWeb } from "../web-search.js";

test("returns error when API key not set", async () => {
  delete process.env.BRAVE_SEARCH_API_KEY;
  const result = await searchWeb({ query: "test" });
  expect(result.text).toContain("ERROR: BRAVE_SEARCH_API_KEY not configured");
});
