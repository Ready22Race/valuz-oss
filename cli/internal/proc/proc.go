// Package proc spawns and supervises the dev-mode subprocesses
// (backend, frontend) that "valuz start" brings up.
package proc

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Spec describes one subprocess valuz should run.
type Spec struct {
	Name string // "backend" / "frontend" — used for log filename + PID record key
	Bin  string // executable, e.g. "uv" or "pnpm"
	Args []string
	Cwd  string
	Env  []string // extra "KEY=VAL" entries on top of os.Environ()

	// ReadyURL, when non-empty, is polled after Spawn returns. The
	// process is considered ready when the URL returns 2xx within
	// ReadyTimeout. Zero ReadyTimeout disables the probe.
	ReadyURL     string
	ReadyTimeout time.Duration
}

// Running holds the runtime handle of a spawned Spec.
type Running struct {
	Spec    Spec
	Cmd     *exec.Cmd
	LogFile string
}

// PidRecord is what we persist to .ai/dev/valuz.pid so valuz stop can
// kill exactly the processes valuz start launched.
type PidRecord struct {
	Backend  int `json:"backend,omitempty"`
	Frontend int `json:"frontend,omitempty"`
}

// LogPath returns the absolute log path for a given spec.
func LogPath(logDir, name string) string {
	return filepath.Join(logDir, name+".log")
}

// Spawn starts every spec as a child process. Output is teed to log
// files under logDir; in foreground mode the same lines are also
// streamed to the parent's stdout/stderr so the user can watch them.
func Spawn(specs []Spec, logDir string, foreground bool) ([]*Running, error) {
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("create log dir: %w", err)
	}

	out := make([]*Running, 0, len(specs))
	for _, s := range specs {
		r, err := start(s, logDir, foreground)
		if err != nil {
			killAll(out)
			return nil, fmt.Errorf("spawn %s: %w", s.Name, err)
		}
		out = append(out, r)
	}

	if err := writePidFile(logDir, out); err != nil {
		killAll(out)
		return nil, err
	}

	probeErr := probe(out)

	if foreground {
		waitForeground(out)
		return out, probeErr
	}
	return out, probeErr
}

func start(s Spec, logDir string, foreground bool) (*Running, error) {
	logPath := LogPath(logDir, s.Name)
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open log file %s: %w", logPath, err)
	}
	_, _ = logFile.WriteString(fmt.Sprintf("\n--- valuz spawn %s at %s ---\n", s.Name, time.Now().Format(time.RFC3339)))

	cmd := exec.Command(s.Bin, s.Args...)
	cmd.Dir = s.Cwd
	cmd.Env = append(os.Environ(), s.Env...)

	if foreground {
		cmd.Stdout = io.MultiWriter(os.Stdout, logFile)
		cmd.Stderr = io.MultiWriter(os.Stderr, logFile)
		cmd.Stdin = os.Stdin
	} else {
		cmd.Stdout = logFile
		cmd.Stderr = logFile
		cmd.Stdin = nil
		// Platform-specific detach (setsid on Unix, CREATE_NEW_PROCESS_GROUP on Windows).
		setDetachAttr(cmd)
	}

	if err := cmd.Start(); err != nil {
		_ = logFile.Close()
		return nil, err
	}
	return &Running{Spec: s, Cmd: cmd, LogFile: logPath}, nil
}

func probe(rs []*Running) error {
	var (
		wg     sync.WaitGroup
		mu     sync.Mutex
		failed []string
	)
	for _, r := range rs {
		if r.Spec.ReadyURL == "" || r.Spec.ReadyTimeout == 0 {
			continue
		}
		wg.Add(1)
		go func(r *Running) {
			defer wg.Done()
			deadline := time.Now().Add(r.Spec.ReadyTimeout)
			client := &http.Client{Timeout: 2 * time.Second}
			for time.Now().Before(deadline) {
				resp, err := client.Get(r.Spec.ReadyURL)
				if err == nil {
					_ = resp.Body.Close()
					if resp.StatusCode < 400 {
						fmt.Fprintf(os.Stderr, "[valuz] %s ready (HTTP %d)\n", r.Spec.Name, resp.StatusCode)
						return
					}
				}
				time.Sleep(time.Second)
			}
			fmt.Fprintf(os.Stderr, "[valuz] %s did not respond within %s — check %s\n",
				r.Spec.Name, r.Spec.ReadyTimeout, r.LogFile)
			mu.Lock()
			failed = append(failed, r.Spec.Name)
			mu.Unlock()
		}(r)
	}
	wg.Wait()
	if len(failed) == 0 {
		return nil
	}
	return fmt.Errorf("readiness probe failed: %s", strings.Join(failed, ", "))
}

func waitForeground(rs []*Running) {
	sigCh := make(chan os.Signal, 1)
	setupSignalNotify(sigCh)

	exitCh := make(chan *Running, len(rs))
	for _, r := range rs {
		go func(r *Running) {
			_ = r.Cmd.Wait()
			exitCh <- r
		}(r)
	}

	select {
	case sig := <-sigCh:
		fmt.Fprintf(os.Stderr, "\n[valuz] received %v, shutting down…\n", sig)
		killAll(rs)
	case r := <-exitCh:
		fmt.Fprintf(os.Stderr, "[valuz] %s exited; stopping siblings\n", r.Spec.Name)
		killAll(rs)
	}

	timeout := time.After(8 * time.Second)
	pending := len(rs) - 1
	for pending > 0 {
		select {
		case <-exitCh:
			pending--
		case <-timeout:
			fmt.Fprintln(os.Stderr, "[valuz] some children did not exit within 8s")
			return
		}
	}
}

func recordedPid(rec PidRecord, name string) int {
	switch name {
	case "backend":
		return rec.Backend
	case "frontend":
		return rec.Frontend
	}
	return 0
}

func killAll(rs []*Running) {
	for _, r := range rs {
		terminateProcess(r.Cmd)
	}
	time.Sleep(5 * time.Second)
	for _, r := range rs {
		killProcess(r.Cmd)
	}
}

const pidFilename = "valuz.pid"

func pidPath(logDir string) string {
	return filepath.Join(logDir, pidFilename)
}

func writePidFile(logDir string, rs []*Running) error {
	rec, _ := ReadPidFile(logDir)
	for _, r := range rs {
		switch r.Spec.Name {
		case "backend":
			rec.Backend = r.Cmd.Process.Pid
		case "frontend":
			rec.Frontend = r.Cmd.Process.Pid
		}
	}
	data, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(pidPath(logDir), data, 0o644); err != nil {
		return fmt.Errorf("write pid file: %w", err)
	}
	return nil
}

// ReadPidFile returns the recorded PIDs (or an empty record if missing).
func ReadPidFile(logDir string) (PidRecord, error) {
	var rec PidRecord
	data, err := os.ReadFile(pidPath(logDir))
	if errors.Is(err, os.ErrNotExist) {
		return rec, nil
	}
	if err != nil {
		return rec, err
	}
	if err := json.Unmarshal(data, &rec); err != nil {
		return rec, err
	}
	return rec, nil
}

// RemovePidFile deletes the PID record.
func RemovePidFile(logDir string) error {
	err := os.Remove(pidPath(logDir))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}

// FormatTail returns the trailing lines of a log file.
func FormatTail(path string, lines int) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	parts := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	if len(parts) > lines {
		parts = parts[len(parts)-lines:]
	}
	return strings.Join(parts, "\n")
}
