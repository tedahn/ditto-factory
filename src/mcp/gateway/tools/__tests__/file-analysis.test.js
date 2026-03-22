import { analyzeFile } from "../file-analysis.js";
import { writeFile, mkdir, rm } from "fs/promises";
import { join } from "path";

const TEST_DIR = "/tmp/gateway-test-workspace";

beforeAll(async () => {
  await mkdir(TEST_DIR, { recursive: true });
  await writeFile(join(TEST_DIR, "test.py"), `import os
import sys

class MyClass:
    def method(self):
        pass  # TODO: implement

def helper():
    return True`);
});

afterAll(async () => {
  await rm(TEST_DIR, { recursive: true, force: true });
});

test("analyzes file structure", async () => {
  process.env.ANALYSIS_BASE_DIR = TEST_DIR;
  const result = await analyzeFile({ file_path: "test.py", analysis_type: "structure" });
  const data = JSON.parse(result.text);
  expect(data.lines).toBeGreaterThan(0);
  expect(data.imports).toContain("import os");
  expect(data.definitions.some(d => d.includes("class MyClass"))).toBe(true);
});

test("analyzes file quality", async () => {
  process.env.ANALYSIS_BASE_DIR = TEST_DIR;
  const result = await analyzeFile({ file_path: "test.py", analysis_type: "quality" });
  const data = JSON.parse(result.text);
  expect(data.quality.todo_count).toBe(1);
});

test("rejects path traversal", async () => {
  process.env.ANALYSIS_BASE_DIR = TEST_DIR;
  const result = await analyzeFile({ file_path: "../../etc/passwd" });
  expect(result.text).toContain("ERROR: Path traversal");
});

test("handles missing file", async () => {
  process.env.ANALYSIS_BASE_DIR = TEST_DIR;
  const result = await analyzeFile({ file_path: "nonexistent.txt" });
  expect(result.text).toContain("ERROR: File not found");
});
