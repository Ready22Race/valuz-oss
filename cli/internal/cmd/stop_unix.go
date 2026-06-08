//go:build !windows

package cmd

import (
	"fmt"
	"os/exec"
	goruntime "runtime"
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
		if pid > 0 && syscall.Kill(pid, 0) == nil {
			_ = syscall.Kill(-pid, syscall.SIGKILL)
			_ = syscall.Kill(pid, syscall.SIGKILL)
			fmt.Printf("[valuz] escalated to SIGKILL pid=%d\n", pid)
		}
	}
}

// quitValuzApp asks Valuz.app to quit via AppleScript (macOS only).
func quitValuzApp() bool {
	if goruntime.GOOS != "darwin" {
		return false
	}
	if err := exec.Command("pgrep", "-f", "Valuz.app/Contents/MacOS").Run(); err != nil {
		return false
	}
	if err := exec.Command("osascript", "-e", `tell application "Valuz" to quit`).Run(); err != nil {
		fmt.Printf("[valuz] warning: osascript quit failed: %v\n", err)
		return false
	}
	return true
}

func stopProcessByPattern(pattern string, force bool) bool {
	sigArg := "-TERM"
	if force {
		sigArg = "-KILL"
	}
	err := exec.Command("pkill", sigArg, "-f", pattern).Run()
	if exitErr, ok := err.(*exec.ExitError); ok {
		return exitErr.ExitCode() == 0
	}
	return err == nil
}
