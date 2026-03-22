/**
 * Web search using Brave Search API.
 * Requires BRAVE_SEARCH_API_KEY env var.
 */
export async function searchWeb({ query, max_results = 5 }) {
  const apiKey = process.env.BRAVE_SEARCH_API_KEY;

  if (!apiKey) {
    return { type: "text", text: "ERROR: BRAVE_SEARCH_API_KEY not configured. Web search is unavailable." };
  }

  try {
    const url = new URL("https://api.search.brave.com/res/v1/web/search");
    url.searchParams.set("q", query);
    url.searchParams.set("count", String(Math.min(max_results, 10)));

    const response = await fetch(url.toString(), {
      headers: {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": apiKey,
      },
    });

    if (!response.ok) {
      return { type: "text", text: `ERROR: Brave Search API returned ${response.status}` };
    }

    const data = await response.json();
    const results = (data.web?.results || []).slice(0, max_results).map(r => ({
      title: r.title,
      url: r.url,
      description: r.description,
    }));

    if (results.length === 0) {
      return { type: "text", text: `No results found for: "${query}"` };
    }

    const formatted = results.map((r, i) =>
      `${i + 1}. **${r.title}**\n   ${r.url}\n   ${r.description}`
    ).join("\n\n");

    return { type: "text", text: `Search results for "${query}":\n\n${formatted}` };
  } catch (err) {
    return { type: "text", text: `ERROR: Search failed: ${err.message}` };
  }
}

export const definition = {
  name: "search_web",
  description: "Search the web using Brave Search API. Returns titles, URLs, and descriptions.",
  inputSchema: {
    type: "object",
    properties: {
      query: { type: "string", description: "Search query" },
      max_results: { type: "number", default: 5, description: "Maximum results (1-10)" },
    },
    required: ["query"],
  },
};
