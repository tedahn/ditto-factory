/**
 * Analyze a file's structure using Node.js fs.
 * Sandboxed to ANALYSIS_BASE_DIR env var.
 * Max file size: 1MB.
 */
export async function analyzeFile({ file_path, analysis_type = "all" }) {
  const fs = await import("fs/promises");
  const path = await import("path");

  // Security: resolve and check against base dir
  const baseDir = process.env.ANALYSIS_BASE_DIR || "/workspace";
  const resolved = path.resolve(baseDir, file_path);
  if (!resolved.startsWith(path.resolve(baseDir))) {
    return { type: "text", text: "ERROR: Path traversal detected. File must be within workspace." };
  }

  try {
    const stat = await fs.stat(resolved);
    if (stat.size > 1_000_000) {
      return { type: "text", text: `ERROR: File too large (${stat.size} bytes). Max 1MB.` };
    }

    const content = await fs.readFile(resolved, "utf-8");
    const lines = content.split("\n");

    const result = {
      path: file_path,
      size_bytes: stat.size,
      lines: lines.length,
      modified: stat.mtime.toISOString(),
    };

    if (analysis_type === "structure" || analysis_type === "all") {
      // Extract imports/requires
      const imports = lines.filter(l =>
        l.match(/^import\s/) || l.match(/^from\s/) || l.match(/require\(/)
      );
      result.imports = imports.map(l => l.trim());

      // Extract function/class definitions
      const definitions = lines.filter(l =>
        l.match(/^(export\s+)?(async\s+)?function\s/) ||
        l.match(/^(export\s+)?class\s/) ||
        l.match(/^(export\s+)?const\s+\w+\s*=\s*(async\s+)?\(/) ||
        l.match(/^\s*(async\s+)?def\s/) ||
        l.match(/^class\s/)
      );
      result.definitions = definitions.map(l => l.trim());
    }

    if (analysis_type === "quality" || analysis_type === "all") {
      // Basic quality metrics
      const todoCount = lines.filter(l => l.match(/TODO|FIXME|HACK|XXX/i)).length;
      const emptyLines = lines.filter(l => l.trim() === "").length;
      const maxLineLength = Math.max(...lines.map(l => l.length));
      result.quality = {
        todo_count: todoCount,
        empty_line_ratio: (emptyLines / lines.length).toFixed(2),
        max_line_length: maxLineLength,
        has_long_lines: maxLineLength > 120,
      };
    }

    return { type: "text", text: JSON.stringify(result, null, 2) };
  } catch (err) {
    if (err.code === "ENOENT") {
      return { type: "text", text: `ERROR: File not found: ${file_path}` };
    }
    return { type: "text", text: `ERROR: ${err.message}` };
  }
}

export const definition = {
  name: "analyze_file",
  description: "Analyze a file's structure, imports, definitions, and quality metrics. Sandboxed to workspace directory.",
  inputSchema: {
    type: "object",
    properties: {
      file_path: { type: "string", description: "Path to file relative to workspace" },
      analysis_type: {
        type: "string",
        enum: ["structure", "quality", "all"],
        default: "all",
        description: "Type of analysis to perform",
      },
    },
    required: ["file_path"],
  },
};
