"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { Loader2, AlertTriangle, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useGitHubStatus } from "@/lib/hooks";

interface ImportUrlInputProps {
  onImport: (url: string, branch: string) => void;
  isImporting: boolean;
  error: string | null;
}

const GITHUB_URL_RE = /^https?:\/\/github\.com\/[\w.-]+\/[\w.-]+/;

export function ImportUrlInput({
  onImport,
  isImporting,
  error,
}: ImportUrlInputProps) {
  const { data: ghStatus } = useGitHubStatus();
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleImport = useCallback(() => {
    if (!url.trim()) {
      setValidationError("Please enter a GitHub URL");
      return;
    }
    if (!GITHUB_URL_RE.test(url.trim())) {
      setValidationError(
        "URL must be a valid GitHub repository (https://github.com/owner/repo)",
      );
      return;
    }
    setValidationError(null);
    onImport(url.trim(), branch.trim() || "main");
  }, [url, branch, onImport]);

  const displayError = validationError || error;

  return (
    <div className="space-y-6">
      {ghStatus && !ghStatus.configured && (
        <div className="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 px-4 py-3">
          <AlertTriangle className="h-4 w-4 text-yellow-500 mt-0.5 shrink-0" />
          <p className="text-sm text-yellow-200">
            No GitHub token configured. Import may be rate-limited.{" "}
            <Link
              href="/toolkits/settings"
              className="underline hover:text-yellow-100"
            >
              Configure token
            </Link>
          </p>
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor="github-url" className="text-sm font-medium text-foreground">
          GitHub Repository URL
        </Label>
        <Input
          id="github-url"
          type="url"
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            if (validationError) setValidationError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !isImporting) handleImport();
          }}
          className="h-12 text-base font-mono"
          disabled={isImporting}
          aria-describedby={displayError ? "url-error" : undefined}
          autoFocus
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="branch" className="text-sm font-medium text-foreground">
          Branch{" "}
          <span className="text-muted-foreground font-normal">(optional, defaults to main)</span>
        </Label>
        <Input
          id="branch"
          type="text"
          placeholder="main"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !isImporting) handleImport();
          }}
          className="max-w-xs"
          disabled={isImporting}
        />
      </div>

      {displayError && (
        <p id="url-error" className="text-sm text-red-400" role="alert">
          {displayError}
        </p>
      )}

      <Button
        onClick={handleImport}
        disabled={isImporting || !url.trim()}
        size="lg"
        className="min-w-[160px]"
      >
        {isImporting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Analyzing repository...
          </>
        ) : (
          <>
            <Download className="h-4 w-4 mr-2" />
            Import
          </>
        )}
      </Button>
    </div>
  );
}
