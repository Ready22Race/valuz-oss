package cmd

import (
	"context"
	"fmt"
	"time"

	"github.com/spf13/cobra"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/proc"
	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/runtime"
)

// Fallback name patterns for when the PID file is missing (e.g. when
// the user previously launched things outside of `valuz start`, or is
// running against a packaged install). Kept in sync with what start.go
// and sidecar.ts spawn.
var stopFallbackPatterns = []string{
	"valuz_agent --host",            // dev: `uv run python -m valuz_agent --host …`
	"valuz-server --host",           // production: PyInstaller bundle started by Electron / launchd
	"pnpm.*--filter @valuz/desktop", // dev frontend
	"concurrently.*vite",            // dev frontend grand-child
}

// RunStop is the package-level entrypoint for "valuz stop" /
// "valuz restart". force=true skips the SIGTERM grace period and
// goes straight to SIGKILL.
func RunStop(force bool) error {
	paths, err := runtime.Discover()
	if err != nil {
		// Tolerate unknown layout — still try the fallback sweep below.
		paths = &runtime.Paths{}
	}

	// Bundle mode → prefer asking Valuz.app to quit cleanly.
	quitGUI := false
	if paths.Mode == runtime.ModeBundle {
		if quitValuzApp() {
			fmt.Println("[valuz] sent quit to Valuz.app (Electron will tear down its sidecar)")
			quitGUI = true
		}
	}

	rec, err := proc.ReadPidFile(paths.LogDir)
	if err != nil {
		fmt.Printf("[valuz] warning: could not read PID file: %v\n", err)
	}

	sig := stopSignal(force)

	signalled := 0
	for label, pid := range map[string]int{"backend": rec.Backend, "frontend": rec.Frontend} {
		if pid <= 0 {
			continue
		}
		if proc.StopByPid(pid, sig) {
			fmt.Printf("[valuz] sent %s to %s (pid=%d)\n", sig, label, pid)
			signalled++
		} else {
			fmt.Printf("[valuz] %s pid=%d not running\n", label, pid)
		}
	}

	// Wait briefly for the recorded PIDs to exit. After grace, escalate
	// to SIGKILL unless we were already KILL'ing.
	if signalled > 0 && !force {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		if rec.Backend > 0 {
			proc.WaitFor(ctx, rec.Backend)
		}
		if rec.Frontend > 0 {
			proc.WaitFor(ctx, rec.Frontend)
		}
		cancel()
		escalatePids(rec)
	}

	// Fallback sweep: anything matching the spawn fingerprints.
	fallbackKilled := false
	for _, pat := range stopFallbackPatterns {
		if stopProcessByPattern(pat, force) {
			fmt.Printf("[valuz] swept %q\n", pat)
			fallbackKilled = true
		}
	}

	if signalled == 0 && !fallbackKilled && !quitGUI {
		fmt.Println("[valuz] no running services found")
	}

	if rerr := proc.RemovePidFile(paths.LogDir); rerr != nil {
		fmt.Printf("[valuz] warning: could not remove PID file: %v\n", rerr)
	}
	return nil
}

func newStopCmd() *cobra.Command {
	var force bool
	c := &cobra.Command{
		Use:   "stop",
		Short: "Stop locally-running runtime services",
		Long: `Stop locally-running runtime services.

Looks up .ai/dev/valuz.pid first (recorded by 'valuz start') and
signals each PID's process group with SIGTERM; falls through to
process-name matching for any leftovers that weren't recorded.`,
		RunE: func(_ *cobra.Command, _ []string) error {
			return RunStop(force)
		},
	}
	c.Flags().BoolVar(&force, "force", false, "Skip the grace period and SIGKILL straight away")
	return c
}
