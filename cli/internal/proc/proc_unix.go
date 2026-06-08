//go:build !windows

package proc

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
	"time"
)

func setupSignalNotify(ch chan<- os.Signal) {
	signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
}

// PrecheckRunning returns the set of services that look alive per the
// recorded PID file.
func PrecheckRunning(logDir string, names []string) ([]string, error) {
	rec, err := ReadPidFile(logDir)
	if err != nil {
		return nil, err
	}
	var conflicts []string
	for _, name := range names {
		pid := recordedPid(rec, name)
		if pid > 0 && syscall.Kill(pid, 0) == nil {
			conflicts = append(conflicts, fmt.Sprintf("%s pid=%d", name, pid))
		}
	}
	return conflicts, nil
}

func setDetachAttr(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
}

func terminateProcess(cmd *exec.Cmd) {
	signalProcess(cmd, syscall.SIGTERM)
}

func killProcess(cmd *exec.Cmd) {
	signalProcess(cmd, syscall.SIGKILL)
}

func signalProcess(cmd *exec.Cmd, sig syscall.Signal) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	if cmd.SysProcAttr != nil && cmd.SysProcAttr.Setsid {
		_ = syscall.Kill(-cmd.Process.Pid, sig)
		return
	}
	_ = cmd.Process.Signal(sig)
}

// StopByPid sends sig to a recorded PID and its process group.
func StopByPid(pid int, sig syscall.Signal) bool {
	if pid <= 0 {
		return false
	}
	if err := syscall.Kill(pid, 0); err != nil {
		return false
	}
	if err := syscall.Kill(-pid, sig); err != nil {
		_ = syscall.Kill(pid, sig)
	}
	return true
}

// WaitFor polls until ctx is done or pid no longer exists.
func WaitFor(ctx context.Context, pid int) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
			if syscall.Kill(pid, 0) != nil {
				return
			}
			time.Sleep(200 * time.Millisecond)
		}
	}
}
