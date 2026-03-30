import { Activity, Bot, ListTodo, Sparkles, Workflow } from "lucide-react";

function StatCard({
  title,
  value,
  icon: Icon,
}: {
  title: string;
  value: string;
  icon: React.ElementType;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <p className="mt-1 text-3xl font-bold text-card-foreground">
            {value}
          </p>
        </div>
        <div className="rounded-lg bg-secondary p-3">
          <Icon className="h-5 w-5 text-secondary-foreground" />
        </div>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-foreground">
          Dashboard
        </h1>
        <p className="mt-1 text-muted-foreground">
          Overview of your Ditto Factory platform.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard title="Active Agents" value="--" icon={Bot} />
        <StatCard title="Running Tasks" value="--" icon={ListTodo} />
        <StatCard title="Skills" value="--" icon={Sparkles} />
        <StatCard title="Workflows" value="--" icon={Workflow} />
      </div>

      <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <Activity className="h-5 w-5 text-muted-foreground" />
          <h2 className="text-lg font-semibold text-card-foreground">
            Recent Activity
          </h2>
        </div>
        <div className="mt-6 flex flex-col items-center justify-center py-12 text-center">
          <Activity className="mb-3 h-10 w-10 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            Activity feed will appear here once the API is connected.
          </p>
        </div>
      </div>
    </div>
  );
}
