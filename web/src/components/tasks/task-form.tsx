"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Loader2, CheckCircle2, Package } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSubmitTaskFull, useToolkits } from "@/lib/hooks";
import { TaskType } from "@/lib/types";

export function TaskForm() {
  const router = useRouter();
  const submitTask = useSubmitTaskFull();

  const [repoOwner, setRepoOwner] = useState("");
  const [repoName, setRepoName] = useState("");
  const [task, setTask] = useState("");
  const [taskType, setTaskType] = useState<TaskType>(TaskType.CODE_CHANGE);
  const [skillOverrides, setSkillOverrides] = useState("");
  const [templateSlug, setTemplateSlug] = useState("");
  const [selectedToolkits, setSelectedToolkits] = useState<string[]>([]);

  const { data: availableToolkits } = useToolkits();

  const toggleToolkit = useCallback((slug: string) => {
    setSelectedToolkits((prev) =>
      prev.includes(slug)
        ? prev.filter((s) => s !== slug)
        : [...prev, slug],
    );
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const payload: {
      repo_owner: string;
      repo_name: string;
      task: string;
      source: string;
      source_ref: Record<string, unknown>;
      task_type: TaskType;
      skill_overrides?: string[];
      template_slug?: string;
      toolkit_slugs?: string[];
    } = {
      repo_owner: repoOwner,
      repo_name: repoName,
      task,
      source: "web",
      source_ref: {},
      task_type: taskType,
    };

    if (skillOverrides.trim()) {
      payload.skill_overrides = skillOverrides
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }

    if (templateSlug.trim()) {
      payload.template_slug = templateSlug.trim();
    }

    if (selectedToolkits.length > 0) {
      payload.toolkit_slugs = selectedToolkits;
    }

    submitTask.mutate(payload, {
      onSuccess: (data: { thread_id?: string }) => {
        if (data?.thread_id) {
          router.push(`/tasks/${data.thread_id}`);
        } else {
          router.push("/tasks");
        }
      },
    });
  };

  const isValid = repoOwner.trim() && repoName.trim() && task.trim();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Submit New Task</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="repo-owner">Repository Owner</Label>
              <Input
                id="repo-owner"
                placeholder="e.g. octocat"
                value={repoOwner}
                onChange={(e) => setRepoOwner(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="repo-name">Repository Name</Label>
              <Input
                id="repo-name"
                placeholder="e.g. hello-world"
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="task-description">Task Description</Label>
            <Textarea
              id="task-description"
              placeholder="Describe the task you want the agent to perform..."
              value={task}
              onChange={(e) => setTask(e.target.value)}
              rows={4}
              required
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="task-type">Task Type</Label>
              <Select
                id="task-type"
                value={taskType}
                onChange={(e) => setTaskType(e.target.value as TaskType)}
              >
                <option value={TaskType.CODE_CHANGE}>Code Change</option>
                <option value={TaskType.ANALYSIS}>Analysis</option>
                <option value={TaskType.DB_MUTATION}>DB Mutation</option>
                <option value={TaskType.FILE_OUTPUT}>File Output</option>
                <option value={TaskType.API_ACTION}>API Action</option>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="template-slug">
                Workflow Template Slug{" "}
                <span className="text-muted-foreground font-normal">
                  (optional)
                </span>
              </Label>
              <Input
                id="template-slug"
                placeholder="e.g. pr-review"
                value={templateSlug}
                onChange={(e) => setTemplateSlug(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="skill-overrides">
              Skill Overrides{" "}
              <span className="text-muted-foreground font-normal">
                (optional, comma-separated)
              </span>
            </Label>
            <Input
              id="skill-overrides"
              placeholder="e.g. python-debug, react-patterns"
              value={skillOverrides}
              onChange={(e) => setSkillOverrides(e.target.value)}
            />
          </div>

          {/* Toolkit Selection */}
          {availableToolkits && availableToolkits.length > 0 && (
            <div className="space-y-2">
              <Label className="flex items-center gap-1.5">
                <Package className="h-4 w-4" />
                Toolkits
                <span className="text-muted-foreground font-normal">
                  (optional)
                </span>
              </Label>
              <p className="text-xs text-muted-foreground -mt-1">
                Select toolkits to include skills and components for this task
              </p>
              <div className="flex flex-wrap gap-2">
                {availableToolkits.map((tk) => (
                  <button
                    key={tk.slug}
                    type="button"
                    onClick={() => toggleToolkit(tk.slug)}
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border transition-colors ${
                      selectedToolkits.includes(tk.slug)
                        ? "bg-primary/15 text-primary border-primary/30"
                        : "bg-muted/50 text-muted-foreground border-border hover:border-muted-foreground/30"
                    }`}
                  >
                    {selectedToolkits.includes(tk.slug) && (
                      <CheckCircle2 className="h-3 w-3" />
                    )}
                    {tk.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {submitTask.isError && (
            <div className="rounded-md bg-red-500/10 border border-red-500/20 px-4 py-3">
              <p className="text-sm text-red-400">
                Failed to submit task.{" "}
                {(submitTask.error as Error)?.message || "Please try again."}
              </p>
            </div>
          )}

          <div className="flex items-center gap-3 pt-2">
            <Button type="submit" disabled={!isValid || submitTask.isPending}>
              {submitTask.isPending && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              Submit Task
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/tasks")}
            >
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
