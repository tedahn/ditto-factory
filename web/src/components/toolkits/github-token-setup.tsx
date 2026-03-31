"use client";

import { useState, useCallback } from "react";
import {
  CheckCircle2,
  Eye,
  EyeOff,
  Loader2,
  Trash2,
  RefreshCw,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import {
  useGitHubStatus,
  useSetGitHubToken,
  useRemoveGitHubToken,
} from "@/lib/hooks";

export function GitHubTokenSetup() {
  const { data: status, isLoading, refetch } = useGitHubStatus();
  const setToken = useSetGitHubToken();
  const removeToken = useRemoveGitHubToken();

  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const handleSave = useCallback(() => {
    if (!tokenInput.trim()) return;
    setToken.mutate(tokenInput.trim(), {
      onSuccess: () => setTokenInput(""),
    });
  }, [tokenInput, setToken]);

  const handleRemove = useCallback(() => {
    if (!confirmRemove) {
      setConfirmRemove(true);
      return;
    }
    removeToken.mutate(undefined, {
      onSuccess: () => setConfirmRemove(false),
    });
  }, [confirmRemove, removeToken]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">
          Checking GitHub status...
        </span>
      </div>
    );
  }

  // Token IS configured
  if (status?.configured) {
    const ratePct =
      status.rate_limit && status.rate_remaining !== null
        ? Math.round((status.rate_remaining / status.rate_limit) * 100)
        : null;

    return (
      <div className="space-y-6">
        {/* Status badge */}
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-emerald-400" />
          <span className="text-sm font-medium text-emerald-400">
            GitHub token configured
          </span>
        </div>

        {/* Rate limit */}
        {status.rate_limit !== null && status.rate_remaining !== null && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Rate limit</span>
              <span className="font-mono">
                {status.rate_remaining.toLocaleString()} /{" "}
                {status.rate_limit.toLocaleString()} remaining
              </span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  ratePct !== null && ratePct < 20
                    ? "bg-red-500"
                    : ratePct !== null && ratePct < 50
                      ? "bg-yellow-500"
                      : "bg-emerald-500"
                }`}
                style={{ width: `${ratePct ?? 0}%` }}
              />
            </div>
          </div>
        )}

        {/* Scopes */}
        {status.scopes && (
          <div className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Scopes:</span>{" "}
            <span className="font-mono">{status.scopes}</span>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3 pt-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Test Connection
          </Button>
          <Button
            variant={confirmRemove ? "destructive" : "outline"}
            size="sm"
            onClick={handleRemove}
            disabled={removeToken.isPending}
          >
            {removeToken.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
            ) : (
              <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            )}
            {confirmRemove ? "Confirm Remove" : "Remove Token"}
          </Button>
          {confirmRemove && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmRemove(false)}
            >
              Cancel
            </Button>
          )}
        </div>
      </div>
    );
  }

  // Token NOT configured
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground leading-relaxed">
        A GitHub personal access token enables toolkit discovery with higher API
        rate limits (5,000/hr vs 60/hr) and access to private repositories.
      </p>

      {/* Steps */}
      <Card className="border-border/50 bg-muted/30">
        <CardContent className="p-4">
          <p className="text-xs font-medium text-foreground mb-3">
            Create a token:
          </p>
          <ol className="space-y-2 text-xs text-muted-foreground list-none">
            <li className="flex gap-2">
              <span className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-muted text-foreground text-[10px] font-semibold shrink-0">
                1
              </span>
              <span>
                Go to{" "}
                <a
                  href="https://github.com/settings/tokens?type=beta"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-400 hover:underline inline-flex items-center gap-0.5"
                >
                  GitHub Settings &rarr; Fine-grained tokens
                  <ExternalLink className="h-3 w-3" />
                </a>
              </span>
            </li>
            <li className="flex gap-2">
              <span className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-muted text-foreground text-[10px] font-semibold shrink-0">
                2
              </span>
              <span>
                Create a new token with{" "}
                <code className="text-foreground bg-muted px-1 rounded">
                  Contents: Read-only
                </code>{" "}
                permission on the repos you want to discover
              </span>
            </li>
            <li className="flex gap-2">
              <span className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-muted text-foreground text-[10px] font-semibold shrink-0">
                3
              </span>
              <span>Copy and paste the token below</span>
            </li>
          </ol>
        </CardContent>
      </Card>

      {/* Token input */}
      <div className="space-y-2">
        <Label
          htmlFor="github-token"
          className="text-sm font-medium text-foreground"
        >
          Personal Access Token
        </Label>
        <div className="relative">
          <Input
            id="github-token"
            type={showToken ? "text" : "password"}
            placeholder="github_pat_..."
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !setToken.isPending) handleSave();
            }}
            className="h-11 font-mono pr-10"
            disabled={setToken.isPending}
            autoComplete="off"
          />
          <button
            type="button"
            onClick={() => setShowToken(!showToken)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            aria-label={showToken ? "Hide token" : "Show token"}
          >
            {showToken ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {/* Error */}
      {setToken.isError && (
        <p className="text-sm text-red-400" role="alert">
          {(setToken.error as Error)?.message || "Failed to save token"}
        </p>
      )}

      {/* Save button */}
      <Button
        onClick={handleSave}
        disabled={setToken.isPending || !tokenInput.trim()}
        className="min-w-[140px]"
      >
        {setToken.isPending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
            Validating...
          </>
        ) : (
          "Save Token"
        )}
      </Button>
    </div>
  );
}
