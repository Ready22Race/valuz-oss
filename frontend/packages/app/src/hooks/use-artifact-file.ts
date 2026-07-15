import { useCallback, useEffect, useRef, useState } from "react";

import {
  buildFileRef,
  filesApi,
  type ArtifactContent,
  type ArtifactDescriptor,
  type PlatformCapabilities,
} from "@valuz/core";
import type { ArtifactOpenTarget } from "@valuz/ui";

import { resolvedToArtifactFile } from "../lib/resolve-artifact";

export interface ArtifactFileLocation {
  /** Absolute identity handed to the file-address resolver. */
  absolutePath: string;
  /** Stable path shown in the shell and kept in page URL state. */
  relativePath: string;
}

interface UseArtifactFileOptions {
  projectId: string | null;
  platform: PlatformCapabilities;
  locate: (path: string) => ArtifactFileLocation;
  missingErrorMessage: string;
}

export interface UseArtifactFileResult {
  selectedPath: string | null;
  artifact: ArtifactDescriptor | null;
  content: ArtifactContent | null;
  target: ArtifactOpenTarget | null;
  loading: boolean;
  error: string | null;
  open: (path: string, target?: ArtifactOpenTarget | null) => Promise<void>;
  reload: () => Promise<void>;
  close: () => void;
}

/**
 * Shared artifact-loader state for project, task, and conversation surfaces.
 *
 * A monotonically increasing request id protects the visible selection even
 * when a transport ignores AbortSignal (notably the local Electron IPC read):
 * a slower request for file A can never overwrite a later request for file B.
 */
export function useArtifactFile({
  projectId,
  platform,
  locate,
  missingErrorMessage,
}: UseArtifactFileOptions): UseArtifactFileResult {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [artifact, setArtifact] = useState<ArtifactDescriptor | null>(null);
  const [content, setContent] = useState<ArtifactContent | null>(null);
  const [target, setTarget] = useState<ArtifactOpenTarget | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  const close = useCallback(() => {
    requestIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setSelectedPath(null);
    setArtifact(null);
    setContent(null);
    setTarget(null);
    setLoading(false);
    setError(null);
  }, []);

  const open = useCallback(
    async (path: string, openTarget?: ArtifactOpenTarget | null) => {
      if (!projectId) return;

      const location = locate(path);
      const requestId = requestIdRef.current + 1;
      requestIdRef.current = requestId;
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      setSelectedPath(location.relativePath);
      setArtifact(null);
      setContent(null);
      setTarget(openTarget ?? null);
      setLoading(true);
      setError(null);

      try {
        const descriptor = await filesApi.resolveOne(
          buildFileRef(location.absolutePath),
          { signal: controller.signal },
        );
        if (requestIdRef.current !== requestId) return;
        if (!descriptor || descriptor.error || !descriptor.exists) {
          setError(missingErrorMessage);
          return;
        }

        const result = await resolvedToArtifactFile(descriptor, {
          projectId,
          relPath: location.relativePath,
          platform,
          signal: controller.signal,
        });
        if (requestIdRef.current !== requestId) return;
        setArtifact(result.artifact);
        setContent(result.content);
      } catch (cause) {
        if (
          requestIdRef.current !== requestId ||
          controller.signal.aborted ||
          (cause instanceof DOMException && cause.name === "AbortError")
        ) {
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (requestIdRef.current === requestId) {
          setLoading(false);
          if (controllerRef.current === controller) {
            controllerRef.current = null;
          }
        }
      }
    },
    [locate, missingErrorMessage, platform, projectId],
  );

  const reload = useCallback(async () => {
    if (selectedPath) await open(selectedPath, target);
  }, [open, selectedPath, target]);

  useEffect(
    () => () => {
      requestIdRef.current += 1;
      controllerRef.current?.abort();
    },
    [],
  );

  return {
    selectedPath,
    artifact,
    content,
    target,
    loading,
    error,
    open,
    reload,
    close,
  };
}
