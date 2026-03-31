"use client";

import { use } from "react";
import { Loader2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import { TemplateEditor } from "@/components/workflows/template-editor";
import { useWorkflowTemplate } from "@/lib/hooks";

export default function EditWorkflowTemplatePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const { data: template, isLoading, isError } = useWorkflowTemplate(slug);

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Edit Workflow Template
            </h1>
            <p className="text-sm text-muted-foreground">
              Update template definition and parameters
            </p>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">
                Loading template...
              </span>
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <p className="text-sm text-destructive-foreground">
                Failed to load template. It may not exist.
              </p>
            </div>
          ) : template ? (
            <TemplateEditor template={template} mode="edit" />
          ) : null}
        </div>
      </div>
    </div>
  );
}
