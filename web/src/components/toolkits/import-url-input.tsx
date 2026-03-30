"use client";

import { useState, useCallback } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface ImportUrlInputProps {
  onDiscover: (url: string, branch?: string) => void;
  isLoading: boolean;
  error: string | null;
}

const GITHUB_URL_RE = /^https?:\/\/github\.com\/[\w.-]+\/[\w.-]+/;

export function ImportUrlInput({
  onDiscover,
  isLoading,
  error,
}: ImportUrlInputProps) {
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleDiscover = useCallback(() => {
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
    onDiscover(url.trim(), branch.trim() || undefined);
  }, [url, branch, onDiscover]);

  const displayError = validationError || error;

  return (
    <div className="space-y-6">
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
            if (e.key === "Enter" && !isLoading) handleDiscover();
          }}
          className="h-12 text-base font-mono"
          disabled={isLoading}
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
            if (e.key === "Enter" && !isLoading) handleDiscover();
          }}
          className="max-w-xs"
          disabled={isLoading}
        />
      </div>

      {displayError && (
        <p id="url-error" className="text-sm text-red-400" role="alert">
          {displayError}
        </p>
      )}

      <Button
        onClick={handleDiscover}
        disabled={isLoading || !url.trim()}
        size="lg"
        className="min-w-[160px]"
      >
        {isLoading ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Analyzing repository...
          </>
        ) : (
          "Discover"
        )}
      </Button>
    </div>
  );
}
