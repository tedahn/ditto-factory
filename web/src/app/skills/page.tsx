"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus, Search } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { SkillTable } from "@/components/skills/skill-table";
import { useSkills, useDeleteSkill } from "@/lib/hooks";

const COMMON_TAGS = ["python", "javascript", "react", "devops", "testing", "debugging"];

export default function SkillsPage() {
  const { data: skills, isLoading, isError } = useSkills();
  const deleteSkill = useDeleteSkill();
  const [searchFilter, setSearchFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");

  const handleTagClick = (tag: string) => {
    setTagFilter(tagFilter === tag ? "" : tag);
  };

  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-foreground">Skills</h1>
            <p className="text-sm text-muted-foreground">
              Manage agent skills and knowledge
            </p>
          </div>
          <Link href="/skills/new">
            <Button size="sm">
              <Plus className="h-4 w-4 mr-1" />
              New Skill
            </Button>
          </Link>
        </div>

        {/* Search and filters */}
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search skills by name, slug, or description..."
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                className="pl-9"
                aria-label="Search skills"
              />
            </div>
            {skills && (
              <span className="text-xs text-muted-foreground font-mono ml-auto">
                {skills.length} total
              </span>
            )}
          </div>

          {/* Tag filter chips */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">Filter by tag:</span>
            {COMMON_TAGS.map((tag) => (
              <button
                key={tag}
                onClick={() => handleTagClick(tag)}
                className="focus:outline-none"
              >
                <Badge
                  variant={tagFilter === tag ? "default" : "secondary"}
                  className="cursor-pointer hover:opacity-80 transition-opacity"
                >
                  {tag}
                </Badge>
              </button>
            ))}
            {tagFilter && !COMMON_TAGS.includes(tagFilter) && (
              <Badge variant="default">{tagFilter}</Badge>
            )}
            {tagFilter && (
              <button
                onClick={() => setTagFilter("")}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            <SkillTable
              skills={skills || []}
              isLoading={isLoading}
              isError={isError}
              searchFilter={searchFilter}
              tagFilter={tagFilter}
              onDelete={(slug) => deleteSkill.mutate(slug)}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
