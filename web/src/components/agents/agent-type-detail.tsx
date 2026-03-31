"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AgentTypeSummary } from "@/lib/types";

interface AgentTypeDetailProps {
  agentType: AgentTypeSummary;
}

export function AgentTypeDetail({ agentType }: AgentTypeDetailProps) {
  return (
    <Card className="col-span-full">
      <CardContent className="p-5 space-y-5">
        {/* Header info */}
        <div className="grid grid-cols-2 gap-4 text-sm">
          <InfoRow label="Name" value={agentType.name} />
          <InfoRow label="Image" value={agentType.image} mono />
          <InfoRow label="Default" value={agentType.is_default ? "Yes" : "No"} />
          <InfoRow label="Total Jobs" value={String(agentType.job_count)} />
          {agentType.description && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Description: </span>
              <span>{agentType.description}</span>
            </div>
          )}
        </div>

        {/* Capabilities */}
        <div>
          <h4 className="text-sm font-medium mb-2">Capabilities</h4>
          {agentType.capabilities.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {agentType.capabilities.map((cap) => (
                <Badge key={cap} variant="outline">{cap}</Badge>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground italic">None defined</p>
          )}
        </div>

        {/* Mapped Skills */}
        <div>
          <h4 className="text-sm font-medium mb-2">Mapped Skills</h4>
          {agentType.mapped_skills.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {agentType.mapped_skills.map((slug) => (
                <Link key={slug} href={`/skills?slug=${slug}`}>
                  <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80">
                    {slug}
                  </Badge>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground italic">No skills map to this type</p>
          )}
        </div>

        {/* Resolution History */}
        <div>
          <h4 className="text-sm font-medium mb-2">Recent Resolutions</h4>
          {agentType.recent_resolutions.length > 0 ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Thread</TableHead>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Required Capabilities</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agentType.recent_resolutions.map((event, i) => (
                    <TableRow key={`${event.thread_id}-${i}`}>
                      <TableCell>
                        <Link
                          href={`/agents/${event.thread_id}`}
                          className="text-primary hover:underline font-mono text-xs"
                        >
                          {event.thread_id.slice(0, 8)}...
                        </Link>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {event.timestamp ? new Date(event.timestamp).toLocaleString() : "--"}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {event.required_capabilities.map((cap) => (
                            <Badge key={cap} variant="outline" className="text-xs">
                              {cap}
                            </Badge>
                          ))}
                          {event.required_capabilities.length === 0 && (
                            <span className="text-xs text-muted-foreground">none</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={event.reason === "best_match" ? "default" : "secondary"}
                          className="text-xs"
                        >
                          {event.reason}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground italic">No resolution events recorded yet</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}: </span>
      <span className={mono ? "font-mono text-xs" : ""}>{value}</span>
    </div>
  );
}
