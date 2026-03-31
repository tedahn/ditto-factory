"use client";

import { use } from "react";
import { Loader2 } from "lucide-react";
import { Header } from "@/components/layout/header";
import { SkillForm } from "@/components/skills/skill-form";
import { VersionHistory } from "@/components/skills/version-history";
import { useSkill } from "@/lib/hooks";

export default function EditSkillPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const { data: skill, isLoading, isError } = useSkill(slug);

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Edit Skill
            </h1>
            <p className="text-sm text-muted-foreground">
              Update skill content and metadata
            </p>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">
                Loading skill...
              </span>
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <p className="text-sm text-destructive-foreground">
                Failed to load skill. It may not exist.
              </p>
            </div>
          ) : skill ? (
            <>
              <SkillForm skill={skill} mode="edit" />
              <VersionHistory
                slug={slug}
                currentVersion={skill.version}
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
