import { useEffect, useRef } from "react";
import { fetchEventSource } from "../api/fetch-event-source";
import { skillsApi } from "../api/skills-api";

const subscribers = new Set<() => void>();
let closeStream: (() => void) | null = null;

function ensureSkillEventStream(): void {
  if (closeStream) return;
  closeStream = fetchEventSource(
    () => skillsApi.eventsStreamUrl(),
    (frame) => {
      if (
        frame.event !== "skill.changed" &&
        frame.event !== "project.skills_changed"
      ) {
        return;
      }
      for (const notify of [...subscribers]) notify();
    },
  );
}

function releaseSkillEventSubscriber(subscriber: () => void): void {
  subscribers.delete(subscriber);
  if (subscribers.size > 0) return;
  closeStream?.();
  closeStream = null;
}

export function useSkillEvents(onSkillChanged?: () => void) {
  const callbackRef = useRef(onSkillChanged);
  callbackRef.current = onSkillChanged;

  useEffect(() => {
    const subscriber = () => callbackRef.current?.();
    subscribers.add(subscriber);
    // fetch-based SSE (not EventSource) so the request carries auth headers.
    ensureSkillEventStream();
    return () => releaseSkillEventSubscriber(subscriber);
  }, []);
}
