"use client";

import { Header } from "@/components/layout/header";
import { StatsCards } from "@/components/dashboard/stats-cards";
import { ActivityFeed } from "@/components/dashboard/activity-feed";
import { QuickSubmit } from "@/components/dashboard/quick-submit";

export default function DashboardPage() {
  return (
    <div className="flex flex-col h-full -m-6">
      <Header />
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <StatsCards />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          <div className="lg:col-span-3">
            <ActivityFeed />
          </div>
          <div className="lg:col-span-2">
            <QuickSubmit />
          </div>
        </div>
      </div>
    </div>
  );
}
