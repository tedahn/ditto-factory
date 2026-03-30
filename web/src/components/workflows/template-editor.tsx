"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useCreateWorkflowTemplate,
  useUpdateWorkflowTemplate,
} from "@/lib/hooks";
import type { WorkflowTemplate } from "@/lib/types";

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

interface TemplateEditorProps {
  template?: WorkflowTemplate;
  mode: "create" | "edit";
}

export function TemplateEditor({ template, mode }: TemplateEditorProps) {
  const router = useRouter();
  const createTemplate = useCreateWorkflowTemplate();
  const updateTemplate = useUpdateWorkflowTemplate(template?.slug || "");

  const [name, setName] = useState(template?.name || "");
  const [slug, setSlug] = useState(template?.slug || "");
  const [slugManual, setSlugManual] = useState(false);
  const [description, setDescription] = useState(template?.description || "");
  const [definitionJson, setDefinitionJson] = useState(() => {
    if (template?.definition) {
      return JSON.stringify(template.definition, null, 2);
    }
    return JSON.stringify(
      {
        steps: [
          {
            name: "step-1",
            task_type: "analysis",
            task_template: "Analyze the codebase",
            depends_on: [],
            parameters: {},
          },
        ],
      },
      null,
      2,
    );
  });
  const [parameterSchemaJson, setParameterSchemaJson] = useState(() => {
    if (template?.parameter_schema) {
      return JSON.stringify(template.parameter_schema, null, 2);
    }
    return JSON.stringify(
      {
        type: "object",
        properties: {},
        required: [],
      },
      null,
      2,
    );
  });
  const [changelog, setChangelog] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  useEffect(() => {
    if (mode === "create" && !slugManual) {
      setSlug(slugify(name));
    }
  }, [name, mode, slugManual]);

  const handleSlugChange = (value: string) => {
    setSlugManual(true);
    setSlug(value);
  };

  const validateJson = useCallback(
    (value: string, setter: (err: string | null) => void): boolean => {
      try {
        JSON.parse(value);
        setter(null);
        return true;
      } catch (e) {
        setter((e as Error).message);
        return false;
      }
    },
    [],
  );

  const handleDefinitionChange = (value: string) => {
    setDefinitionJson(value);
    validateJson(value, setJsonError);
  };

  const handleSchemaChange = (value: string) => {
    setParameterSchemaJson(value);
    validateJson(value, setSchemaError);
  };

  const mutation = mode === "create" ? createTemplate : updateTemplate;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const defValid = validateJson(definitionJson, setJsonError);
    const schemaValid = validateJson(parameterSchemaJson, setSchemaError);
    if (!defValid || !schemaValid) return;

    const definition = JSON.parse(definitionJson);
    const parameter_schema = JSON.parse(parameterSchemaJson);

    if (mode === "create") {
      createTemplate.mutate(
        {
          name,
          slug,
          description,
          definition,
          parameter_schema,
          created_by: "web",
        },
        {
          onSuccess: () => router.push("/workflows"),
        },
      );
    } else {
      updateTemplate.mutate(
        {
          description,
          definition,
          parameter_schema,
          ...(changelog ? { changelog } : {}),
          updated_by: "web",
        },
        {
          onSuccess: () => router.push("/workflows"),
        },
      );
    }
  };

  const isValid =
    mode === "create"
      ? name.trim() && slug.trim() && !jsonError && !schemaError
      : !jsonError && !schemaError;

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Metadata section */}
      <Card>
        <CardHeader>
          <CardTitle>Template Metadata</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="template-name">Name</Label>
              <Input
                id="template-name"
                placeholder="e.g. Full Code Review"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={mode === "edit"}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="template-slug">Slug</Label>
              <Input
                id="template-slug"
                placeholder="e.g. full-code-review"
                value={slug}
                onChange={(e) => handleSlugChange(e.target.value)}
                required
                disabled={mode === "edit"}
                className="font-mono"
              />
              {mode === "create" && !slugManual && (
                <p className="text-xs text-muted-foreground">
                  Auto-generated from name
                </p>
              )}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="template-description">Description</Label>
            <Textarea
              id="template-description"
              placeholder="Describe what this workflow does..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </div>
          {mode === "edit" && (
            <div className="space-y-2">
              <Label htmlFor="template-changelog">
                Changelog{" "}
                <span className="text-muted-foreground font-normal">
                  (optional)
                </span>
              </Label>
              <Input
                id="template-changelog"
                placeholder="What changed in this version?"
                value={changelog}
                onChange={(e) => setChangelog(e.target.value)}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* DAG Definition */}
      <Card>
        <CardHeader>
          <CardTitle>DAG Definition</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Label htmlFor="template-definition">
              Workflow Steps (JSON)
            </Label>
            <div className="relative">
              <Textarea
                id="template-definition"
                value={definitionJson}
                onChange={(e) => handleDefinitionChange(e.target.value)}
                rows={16}
                required
                className="font-mono text-sm resize-y min-h-[300px] leading-relaxed"
                spellCheck={false}
              />
            </div>
            {jsonError && (
              <p className="text-xs text-red-400">
                Invalid JSON: {jsonError}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Parameter Schema */}
      <Card>
        <CardHeader>
          <CardTitle>Parameter Schema</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Label htmlFor="template-schema">
              JSON Schema for runtime parameters
            </Label>
            <Textarea
              id="template-schema"
              value={parameterSchemaJson}
              onChange={(e) => handleSchemaChange(e.target.value)}
              rows={10}
              className="font-mono text-sm resize-y min-h-[200px] leading-relaxed"
              spellCheck={false}
            />
            {schemaError && (
              <p className="text-xs text-red-400">
                Invalid JSON: {schemaError}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {mutation.isError && (
        <div className="rounded-md bg-red-500/10 border border-red-500/20 px-4 py-3">
          <p className="text-sm text-red-400">
            Failed to {mode === "create" ? "create" : "update"} template.{" "}
            {(mutation.error as Error)?.message || "Please try again."}
          </p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!isValid || mutation.isPending}>
          {mutation.isPending && (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          )}
          {mode === "create" ? "Create Template" : "Save Changes"}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => router.push("/workflows")}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
