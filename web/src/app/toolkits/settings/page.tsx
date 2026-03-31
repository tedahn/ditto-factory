"use client";

import Link from "next/link";
import { ArrowLeft, Github } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { GitHubTokenSetup } from "@/components/toolkits/github-token-setup";

export default function ToolkitSettingsPage() {
  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Back link */}
        <Link
          href="/toolkits"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Toolkits
        </Link>

        {/* Page header */}
        <div>
          <div className="flex items-center gap-2">
            <Github className="h-5 w-5 text-foreground" />
            <h1 className="text-lg font-semibold text-foreground">
              GitHub Integration
            </h1>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Configure your GitHub personal access token for toolkit discovery
            and private repository access.
          </p>
        </div>

        {/* Main settings card */}
        <Card className="max-w-2xl">
          <CardHeader className="pb-4">
            <h2 className="text-sm font-medium text-foreground">
              Personal Access Token
            </h2>
          </CardHeader>
          <CardContent>
            <GitHubTokenSetup />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
