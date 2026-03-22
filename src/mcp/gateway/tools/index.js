import { analyzeFile, definition as fileAnalysisDef } from "./file-analysis.js";
import { searchWeb, definition as webSearchDef } from "./web-search.js";
import { queryDatabase, definition as dbQueryDef } from "./db-query.js";

export const TOOL_REGISTRY = {
  "file-analysis": { ...fileAnalysisDef, handler: analyzeFile },
  "web-search": { ...webSearchDef, handler: searchWeb },
  "db-query": { ...dbQueryDef, handler: queryDatabase },
};
