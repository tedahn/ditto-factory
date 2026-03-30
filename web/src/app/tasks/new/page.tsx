"use client";

import { Header } from "@/components/layout/header";
import { TaskForm } from "@/components/tasks/task-form";

export default function NewTaskPage() {
  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl mx-auto space-y-6">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              New Task
            </h1>
            <p className="text-sm text-muted-foreground">
              Submit a new task for an agent to execute
            </p>
          </div>
          <TaskForm />
        </div>
      </div>
    </div>
  );
}
