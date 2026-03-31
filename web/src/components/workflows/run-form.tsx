"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Play, Calculator } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  useStartWorkflowExecution,
  useEstimateWorkflow,
} from "@/lib/hooks";
import type { WorkflowTemplate, WorkflowEstimate } from "@/lib/types";

interface ParameterProperty {
  type?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
}

interface RunFormProps {
  template: WorkflowTemplate;
}

export function RunForm({ template }: RunFormProps) {
  const router = useRouter();
  const startExecution = useStartWorkflowExecution();
  const estimateWorkflow = useEstimateWorkflow();

  const [estimate, setEstimate] = useState<WorkflowEstimate | null>(null);

  // Parse parameter schema to build dynamic form
  const parameterFields = useMemo(() => {
    const schema = template.parameter_schema as {
      properties?: Record<string, ParameterProperty>;
      required?: string[];
    } | null;
    if (!schema?.properties) return [];
    const required = schema.required || [];
    return Object.entries(schema.properties).map(([key, prop]) => ({
      key,
      type: prop.type || "string",
      description: prop.description || "",
      defaultValue: prop.default,
      enumValues: prop.enum,
      required: required.includes(key),
    }));
  }, [template.parameter_schema]);

  const [paramValues, setParamValues] = useState<Record<string, string>>(() => {
    const defaults: Record<string, string> = {};
    parameterFields.forEach((field) => {
      if (field.defaultValue !== undefined) {
        defaults[field.key] = String(field.defaultValue);
      } else {
        defaults[field.key] = "";
      }
    });
    return defaults;
  });

  const buildParameters = (): Record<string, unknown> => {
    const params: Record<string, unknown> = {};
    parameterFields.forEach((field) => {
      const val = paramValues[field.key];
      if (val === "" && !field.required) return;
      switch (field.type) {
        case "number":
        case "integer":
          params[field.key] = Number(val) || 0;
          break;
        case "boolean":
          params[field.key] = val === "true";
          break;
        case "object":
        case "array":
          try {
            params[field.key] = JSON.parse(val);
          } catch {
            params[field.key] = val;
          }
          break;
        default:
          params[field.key] = val;
      }
    });
    return params;
  };

  const handleEstimate = () => {
    const parameters = buildParameters();
    estimateWorkflow.mutate(
      { template_slug: template.slug, parameters },
      {
        onSuccess: (data) => setEstimate(data),
      },
    );
  };

  const handleExecute = () => {
    const parameters = buildParameters();
    startExecution.mutate(
      {
        template_slug: template.slug,
        parameters,
        triggered_by: "web",
      },
      {
        onSuccess: (data) => {
          router.push(`/workflows/executions/${data.execution_id}`);
        },
      },
    );
  };

  const updateParam = (key: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="space-y-6">
      {/* Template info */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>{template.name}</span>
            <Badge variant="info">{template.slug}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {template.description && (
            <p className="text-sm text-muted-foreground">
              {template.description}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Dynamic parameter form */}
      {parameterFields.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Parameters</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {parameterFields.map((field) => (
              <div key={field.key} className="space-y-2">
                <Label htmlFor={`param-${field.key}`}>
                  {field.key}
                  {field.required && (
                    <span className="text-red-400 ml-1">*</span>
                  )}
                  {field.description && (
                    <span className="text-muted-foreground font-normal ml-2">
                      ({field.description})
                    </span>
                  )}
                </Label>
                {field.enumValues ? (
                  <select
                    id={`param-${field.key}`}
                    value={paramValues[field.key] || ""}
                    onChange={(e) => updateParam(field.key, e.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    <option value="">Select...</option>
                    {field.enumValues.map((val) => (
                      <option key={val} value={val}>
                        {val}
                      </option>
                    ))}
                  </select>
                ) : field.type === "object" || field.type === "array" ? (
                  <Textarea
                    id={`param-${field.key}`}
                    value={paramValues[field.key] || ""}
                    onChange={(e) => updateParam(field.key, e.target.value)}
                    rows={4}
                    className="font-mono text-sm"
                    placeholder={`Enter ${field.type} as JSON...`}
                    spellCheck={false}
                  />
                ) : field.type === "boolean" ? (
                  <select
                    id={`param-${field.key}`}
                    value={paramValues[field.key] || "false"}
                    onChange={(e) => updateParam(field.key, e.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : (
                  <Input
                    id={`param-${field.key}`}
                    type={
                      field.type === "number" || field.type === "integer"
                        ? "number"
                        : "text"
                    }
                    value={paramValues[field.key] || ""}
                    onChange={(e) => updateParam(field.key, e.target.value)}
                    placeholder={`Enter ${field.key}...`}
                  />
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {parameterFields.length === 0 && (
        <Card>
          <CardContent className="py-6">
            <p className="text-sm text-muted-foreground text-center">
              This workflow has no configurable parameters.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Cost estimate */}
      {estimate && (
        <Card>
          <CardHeader>
            <CardTitle>Estimate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Total Steps</p>
                <p className="text-lg font-mono">{estimate.total_steps}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">
                  Parallel Groups
                </p>
                <p className="text-lg font-mono">
                  {estimate.parallel_groups}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Est. Duration</p>
                <p className="text-lg font-mono">
                  {estimate.estimated_duration_seconds}s
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Est. Agents</p>
                <p className="text-lg font-mono">
                  {estimate.estimated_agents}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Error display */}
      {(startExecution.isError || estimateWorkflow.isError) && (
        <div className="rounded-md bg-red-500/10 border border-red-500/20 px-4 py-3">
          <p className="text-sm text-red-400">
            {startExecution.isError
              ? `Failed to start execution: ${(startExecution.error as Error)?.message || "Unknown error"}`
              : `Failed to estimate: ${(estimateWorkflow.error as Error)?.message || "Unknown error"}`}
          </p>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-3">
        <Button
          onClick={handleEstimate}
          variant="outline"
          disabled={estimateWorkflow.isPending}
        >
          {estimateWorkflow.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          ) : (
            <Calculator className="h-4 w-4 mr-1" />
          )}
          Estimate Cost
        </Button>
        <Button
          onClick={handleExecute}
          disabled={startExecution.isPending}
        >
          {startExecution.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          ) : (
            <Play className="h-4 w-4 mr-1" />
          )}
          Execute Workflow
        </Button>
        <Button
          variant="outline"
          onClick={() => router.push("/workflows")}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
