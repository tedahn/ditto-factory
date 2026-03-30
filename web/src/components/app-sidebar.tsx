"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  ListTodo,
  Sparkles,
  Workflow,
  Bot,
  Factory,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Tasks", href: "/tasks", icon: ListTodo },
  { label: "Skills", href: "/skills", icon: Sparkles },
  { label: "Workflows", href: "/workflows", icon: Workflow },
  { label: "Agents", href: "/agents", icon: Bot },
];

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-64 flex-col border-r border-sidebar-border bg-sidebar-background">
      <div className="flex h-14 items-center gap-2 border-b border-sidebar-border px-4">
        <Factory className="h-6 w-6 text-sidebar-primary" />
        <span className="text-lg font-semibold text-sidebar-foreground">
          Ditto Factory
        </span>
      </div>

      <nav className="flex-1 space-y-1 p-3" role="navigation" aria-label="Main navigation">
        {navItems.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              )}
              aria-current={isActive ? "page" : undefined}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-sidebar-border p-4">
        <p className="text-xs text-muted-foreground">Ditto Factory v0.1.0</p>
      </div>
    </aside>
  );
}
