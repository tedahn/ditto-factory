"use client";

import { Header } from "@/components/layout/header";
import { SkillForm } from "@/components/skills/skill-form";

export default function NewSkillPage() {
  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              New Skill
            </h1>
            <p className="text-sm text-muted-foreground">
              Create a new skill for agent use
            </p>
          </div>
          <SkillForm mode="create" />
        </div>
      </div>
    </div>
  );
}
