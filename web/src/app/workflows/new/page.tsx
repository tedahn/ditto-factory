"use client";

import { Header } from "@/components/layout/header";
import { TemplateEditor } from "@/components/workflows/template-editor";

export default function NewWorkflowTemplatePage() {
  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              New Workflow Template
            </h1>
            <p className="text-sm text-muted-foreground">
              Define a new workflow with steps and parameters
            </p>
          </div>
          <TemplateEditor mode="create" />
        </div>
      </div>
    </div>
  );
}
