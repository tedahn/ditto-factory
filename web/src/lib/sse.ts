"use client";

import { useState, useEffect, useRef, useCallback } from "react";

export interface SSEEvent {
  id?: string;
  event: string;
  data: string;
  timestamp: number;
}

export type SSEStatus = "connecting" | "connected" | "disconnected" | "error";

interface UseEventSourceOptions {
  /** Whether the connection is enabled (default: true) */
  enabled?: boolean;
  /** Max number of events to keep in memory (default: 1000) */
  maxEvents?: number;
  /** Reconnect delay in ms (default: 3000) */
  reconnectDelay?: number;
  /** Max reconnect attempts (default: 10) */
  maxReconnectAttempts?: number;
}

interface UseEventSourceReturn {
  events: SSEEvent[];
  status: SSEStatus;
  error: string | null;
  clearEvents: () => void;
}

export function useEventSource(
  url: string | null,
  options: UseEventSourceOptions = {},
): UseEventSourceReturn {
  const {
    enabled = true,
    maxEvents = 1000,
    reconnectDelay = 3000,
    maxReconnectAttempts = 10,
  } = options;

  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [status, setStatus] = useState<SSEStatus>("disconnected");
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  useEffect(() => {
    if (!url || !enabled) {
      setStatus("disconnected");
      return;
    }

    function connect() {
      setStatus("connecting");
      setError(null);

      const es = new EventSource(url!);
      eventSourceRef.current = es;

      es.onopen = () => {
        setStatus("connected");
        reconnectAttemptsRef.current = 0;
      };

      es.onmessage = (event) => {
        const sseEvent: SSEEvent = {
          event: "message",
          data: event.data,
          timestamp: Date.now(),
        };
        setEvents((prev) => {
          const next = [...prev, sseEvent];
          return next.length > maxEvents ? next.slice(-maxEvents) : next;
        });
      };

      // Listen for typed events
      const eventTypes = ["job_status", "log_line", "result"];
      for (const type of eventTypes) {
        es.addEventListener(type, (event: MessageEvent) => {
          const sseEvent: SSEEvent = {
            event: type,
            data: event.data,
            timestamp: Date.now(),
          };
          setEvents((prev) => {
            const next = [...prev, sseEvent];
            return next.length > maxEvents ? next.slice(-maxEvents) : next;
          });
        });
      }

      es.onerror = () => {
        es.close();
        eventSourceRef.current = null;

        if (reconnectAttemptsRef.current < maxReconnectAttempts) {
          setStatus("connecting");
          reconnectAttemptsRef.current += 1;
          reconnectTimerRef.current = setTimeout(connect, reconnectDelay);
        } else {
          setStatus("error");
          setError("Max reconnection attempts reached");
        }
      };
    }

    connect();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [url, enabled, maxEvents, reconnectDelay, maxReconnectAttempts]);

  return { events, status, error, clearEvents };
}
