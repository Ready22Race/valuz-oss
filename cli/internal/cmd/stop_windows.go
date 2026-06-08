package cmd

import (
	"fmt"
	"syscall"

	"code.xiaobangtouzi.com/valuz/valuz-oss/cli/internal/proc"
)

func stopSignal(force bool) syscall.Signal {
	if force {
		return syscall.SIGKILL
	}
	return syscall.SIGTERM
}

func escalatePids(rec proc.PidRecord) {
	for _, pid := range []int{rec.Backend, rec.Frontend} {
		if pid > 0 && proc.StopByPid(pid, syscall.SIGKILL) {
			fmt.Printf("[valuz] escalated to SIGKILL pid=%d\n", pid)
		}
	}
}

func quitValuzApp() bool {
	return false
}

func stopProcessByPattern(pattern string, force bool) bool {
	// No pkill on Windows; fallback sweep is Unix-only.
	return false
}
