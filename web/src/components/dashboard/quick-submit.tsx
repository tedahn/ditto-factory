"use client";

import { useState, useCallback } from "react";
import { Send, Loader2, CheckCircle2, Package } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useSubmitTask, useToolkits } from "@/lib/hooks";

export function QuickSubmit() {
  const [repoOwner, setRepoOwner] = useState("");
  const [repoName, setRepoName] = useState("");
  const [task, setTask] = useState("");
  const [showSuccess, setShowSuccess] = useState(false);
  const [selectedToolkits, setSelectedToolkits] = useState<string[]>([]);

  const submitTask = useSubmitTask();
  const { data: toolkits } = useToolkits();

  const toggleToolkit = useCallback((slug: string) => {
    setSelectedToolkits((prev) =>
      prev.includes(slug)
        ? prev.filter((s) => s !== slug)
        : [...prev, slug],
    );
  }, []);

  const canSubmit =
    repoOwner.trim() !== "" &&
    repoName.trim() !== "" &&
    task.trim() !== "" &&
    !submitTask.isPending;

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!canSubmit) return;

      submitTask.mutate(
        {
          repo_owner: repoOwner.trim(),
          repo_name: repoName.trim(),
          task: task.trim(),
          ...(selectedToolkits.length > 0 && {
            toolkit_slugs: selectedToolkits,
          }),
        },
        {
          onSuccess: () => {
            setRepoOwner("");
            setRepoName("");
            setTask("");
            setSelectedToolkits([]);
            setShowSuccess(true);
            setTimeout(() => setShowSuccess(false), 3000);
          },
        },
      );
    },
    [canSubmit, repoOwner, repoName, task, submitTask],
  );

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <Send className="h-4 w-4 text-muted-foreground" />
          <CardTitle>Quick Submit</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="repo-owner" className="text-xs">
                Repository Owner
              </Label>
              <Input
                id="repo-owner"
                placeholder="e.g. acme-corp"
                value={repoOwner}
                onChange={(e) => setRepoOwner(e.target.value)}
                className="h-8 text-sm font-mono"
                disabled={submitTask.isPending}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="repo-name" className="text-xs">
                Repository Name
              </Label>
              <Input
                id="repo-name"
                placeholder="e.g. backend-api"
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                className="h-8 text-sm font-mono"
                disabled={submitTask.isPending}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="task-desc" className="text-xs">
              Task Description
            </Label>
            <Textarea
              id="task-desc"
              placeholder="Describe the task for the agent..."
              value={task}
              onChange={(e) => setTask(e.target.value)}
              rows={3}
              className="text-sm resize-none"
              disabled={submitTask.isPending}
            />
          </div>
          {/* Toolkit Selection */}
          {toolkits && toolkits.length > 0 && (
            <div className="space-y-1.5">
              <Label className="text-xs flex items-center gap-1">
                <Package className="h-3 w-3" />
                Toolkits
                <span className="text-muted-foreground font-normal">
                  (optional)
                </span>
              </Label>
              <div className="flex flex-wrap gap-1.5">
                {toolkits.map((tk) => (
                  <button
                    key={tk.slug}
                    type="button"
                    onClick={() => toggleToolkit(tk.slug)}
                    className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium border transition-colors ${
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

          <div className="flex items-center gap-3">
            <Button
              type="submit"
              size="sm"
              disabled={!canSubmit}
              className="gap-1.5"
            >
              {submitTask.isPending ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Submitting...
                </>
              ) : (
                <>
                  <Send className="h-3.5 w-3.5" />
                  Submit Task
                </>
              )}
            </Button>
            {showSuccess && (
              <span className="flex items-center gap-1 text-xs text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Task submitted
              </span>
            )}
            {submitTask.isError && (
              <span className="text-xs text-red-400">
                Failed to submit. Check connection.
              </span>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
