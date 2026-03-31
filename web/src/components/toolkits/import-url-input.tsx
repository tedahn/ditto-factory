"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { Loader2, AlertTriangle, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useGitHubStatus } from "@/lib/hooks";

interface ImportUrlInputProps {
  onDiscover: (url: string, branch?: string) => void;
  onAiOnboard?: (url: string, branch: string) => void;
  isLoading: boolean;
  isOnboarding?: boolean;
  error: string | null;
}

const GITHUB_URL_RE = /^https?:\/\/github\.com\/[\w.-]+\/[\w.-]+/;

export function ImportUrlInput({
  onDiscover,
  onAiOnboard,
  isLoading,
  isOnboarding,
  error,
}: ImportUrlInputProps) {
  const { data: ghStatus } = useGitHubStatus();
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
      {ghStatus && !ghStatus.configured && (
        <div className="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 px-4 py-3">
          <AlertTriangle className="h-4 w-4 text-yellow-500 mt-0.5 shrink-0" />
          <p className="text-sm text-yellow-200">
            No GitHub token configured. Discovery may be rate-limited.{" "}
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

      <div className="flex gap-3">
        <Button
          onClick={handleDiscover}
          disabled={isLoading || isOnboarding || !url.trim()}
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

        {onAiOnboard && (
          <Button
            variant="outline"
            onClick={() => {
              if (!url.trim()) return;
              if (!GITHUB_URL_RE.test(url.trim())) {
                setValidationError(
                  "URL must be a valid GitHub repository (https://github.com/owner/repo)",
                );
                return;
              }
              setValidationError(null);
              onAiOnboard(url.trim(), branch.trim() || "main");
            }}
            disabled={!url.trim() || isLoading || isOnboarding}
            size="lg"
            className="min-w-[160px]"
          >
            {isOnboarding ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Analyzing with AI...
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4 mr-2" />
                AI Onboard
              </>
            )}
          </Button>
        )}
      </div>
    </div>
  );
}
