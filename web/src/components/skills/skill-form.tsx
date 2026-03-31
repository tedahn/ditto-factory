"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useCreateSkill, useUpdateSkill } from "@/lib/hooks";
import type { Skill } from "@/lib/types";

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

interface SkillFormProps {
  skill?: Skill;
  mode: "create" | "edit";
}

export function SkillForm({ skill, mode }: SkillFormProps) {
  const router = useRouter();
  const createSkill = useCreateSkill();
  const updateSkill = useUpdateSkill(skill?.slug || "");

  const [name, setName] = useState(skill?.name || "");
  const [slug, setSlug] = useState(skill?.slug || "");
  const [slugManual, setSlugManual] = useState(false);
  const [description, setDescription] = useState(skill?.description || "");
  const [content, setContent] = useState(skill?.content || "");
  const [tagsInput, setTagsInput] = useState(skill?.tags?.join(", ") || "");
  const [language, setLanguage] = useState(skill?.language || "");
  const [domain, setDomain] = useState(skill?.domain || "");
  const [changelog, setChangelog] = useState("");

  useEffect(() => {
    if (mode === "create" && !slugManual) {
      setSlug(slugify(name));
    }
  }, [name, mode, slugManual]);

  const handleSlugChange = (value: string) => {
    setSlugManual(true);
    setSlug(value);
  };

  const mutation = mode === "create" ? createSkill : updateSkill;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    if (mode === "create") {
      createSkill.mutate(
        {
          name,
          slug,
          description,
          content,
          tags,
          ...(language ? { language } : {}),
          ...(domain ? { domain } : {}),
        },
        {
          onSuccess: () => router.push("/skills"),
        },
      );
    } else {
      updateSkill.mutate(
        {
          description,
          content,
          tags,
          ...(language ? { language } : {}),
          ...(domain ? { domain } : {}),
          ...(changelog ? { changelog } : {}),
        },
        {
          onSuccess: () => router.push("/skills"),
        },
      );
    }
  };

  const isValid =
    mode === "create"
      ? name.trim() && slug.trim() && content.trim()
      : content.trim();

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left column: Metadata */}
        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="skill-name">Name</Label>
              <Input
                id="skill-name"
                placeholder="e.g. React Patterns"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={mode === "edit"}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="skill-slug">Slug</Label>
              <Input
                id="skill-slug"
                placeholder="e.g. react-patterns"
                value={slug}
                onChange={(e) => handleSlugChange(e.target.value)}
                required
                disabled={mode === "edit"}
                className="font-mono"
              />
              {mode === "create" && !slugManual && (
                <p className="text-xs text-muted-foreground">
                  Auto-generated from name
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="skill-description">Description</Label>
              <Textarea
                id="skill-description"
                placeholder="Brief description of what this skill does..."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="skill-tags">
                Tags{" "}
                <span className="text-muted-foreground font-normal">
                  (comma-separated)
                </span>
              </Label>
              <Input
                id="skill-tags"
                placeholder="e.g. react, frontend, patterns"
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="skill-language">
                  Language{" "}
                  <span className="text-muted-foreground font-normal">
                    (optional)
                  </span>
                </Label>
                <Input
                  id="skill-language"
                  placeholder="e.g. python"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="skill-domain">
                  Domain{" "}
                  <span className="text-muted-foreground font-normal">
                    (optional)
                  </span>
                </Label>
                <Input
                  id="skill-domain"
                  placeholder="e.g. web-dev"
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                />
              </div>
            </div>

            {mode === "edit" && (
              <div className="space-y-2">
                <Label htmlFor="skill-changelog">
                  Changelog{" "}
                  <span className="text-muted-foreground font-normal">
                    (optional)
                  </span>
                </Label>
                <Input
                  id="skill-changelog"
                  placeholder="What changed in this version?"
                  value={changelog}
                  onChange={(e) => setChangelog(e.target.value)}
                />
              </div>
            )}
          </CardContent>
        </Card>

        {/* Right column: Content */}
        <Card>
          <CardHeader>
            <CardTitle>Content</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <Label htmlFor="skill-content">
                Skill Content{" "}
                <span className="text-muted-foreground font-normal">
                  (markdown)
                </span>
              </Label>
              <Textarea
                id="skill-content"
                placeholder="Write the skill content in markdown..."
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={20}
                required
                className="font-mono text-sm resize-y min-h-[400px]"
              />
            </div>
          </CardContent>
        </Card>
      </div>

      {mutation.isError && (
        <div className="rounded-md bg-red-500/10 border border-red-500/20 px-4 py-3">
          <p className="text-sm text-red-400">
            Failed to {mode === "create" ? "create" : "update"} skill.{" "}
            {(mutation.error as Error)?.message || "Please try again."}
          </p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!isValid || mutation.isPending}>
          {mutation.isPending && (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          )}
          {mode === "create" ? "Create Skill" : "Save Changes"}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => router.push("/skills")}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
