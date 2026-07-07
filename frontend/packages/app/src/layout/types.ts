/**
 * ProjectOutletContext — the interface pages use to communicate
 * with their parent layout (set right panel, header, etc.).
 *
 * Desktop's DesktopProjectLayout provides this via <Outlet context={...}>.
 * Webui can provide its own simpler layout.
 */

import type { ReactNode } from "react";
import { useOutletContext } from "react-router-dom";

export interface ProjectOutletContext {
  setRightPanel: (node: ReactNode | null) => void;
  setHeader: (node: ReactNode | null) => void;
  setHeaderClassName: (cls: string | undefined) => void;
  setHideHeader: (hide: boolean) => void;
  setAsideClassName: (cls: string | undefined) => void;
  setMainClassName: (cls: string | undefined) => void;
  setContentInnerClassName: (cls: string | undefined) => void;
}

export function useProjectOutlet(): ProjectOutletContext {
  return useOutletContext<ProjectOutletContext>();
}

/**
 * Like {@link useProjectOutlet}, but tolerant of the outlet being absent —
 * returns ``undefined`` when the component renders outside a project
 * ``<Outlet context>`` subtree (e.g. AgentDetailView inside the agent-library
 * master-detail right panel, which the layout mounts in its aside slot, NOT
 * under the Outlet). Callers guard the setters instead of crashing on a
 * destructure of ``undefined``. ``useOutletContext`` yields ``null`` when no
 * context is provided; normalize that to ``undefined``.
 */
export function useOptionalProjectOutlet(): ProjectOutletContext | undefined {
  return useOutletContext<ProjectOutletContext | null>() ?? undefined;
}
