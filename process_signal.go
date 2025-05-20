package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"go.uber.org/zap"

	"github.com/bifrostinc/code-sync-sidecar/log"
)

// ProcessSignaler is an interface for sending signals to processes
type ProcessSignaler interface {
	Signal(sig syscall.Signal) error
}

// OSProcess wraps os.Process to implement ProcessSignaler
type OSProcess struct {
	*os.Process
}

func (p *OSProcess) Signal(sig syscall.Signal) error {
	return p.Process.Signal(sig)
}

// ProcessFinder is an interface for finding processes
type ProcessFinder interface {
	FindProcess(pid int) (ProcessSignaler, error)
}

// DefaultProcessFinder uses os.FindProcess
type DefaultProcessFinder struct{}

func (f *DefaultProcessFinder) FindProcess(pid int) (ProcessSignaler, error) {
	proc, err := os.FindProcess(pid)
	if err != nil {
		return nil, err
	}
	return &OSProcess{proc}, nil
}

func sendSignalToLauncher(watchDir string, processFinder ProcessFinder) error {
	pidFile := filepath.Join(getLauncherDir(watchDir), "launcher.pid")
	pidBytes, err := os.ReadFile(pidFile)
	if err != nil {
		return fmt.Errorf("failed to read pid file: %w", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(pidBytes)))
	if err != nil {
		return fmt.Errorf("failed to convert pid to int: %w", err)
	}

	log.Info("Sending SIGHUP signal to pid", zap.Int("pid", pid))
	process, err := processFinder.FindProcess(pid)
	if err != nil {
		return fmt.Errorf("failed to find process: %w", err)
	}
	err = process.Signal(syscall.SIGHUP)
	if err != nil {
		return fmt.Errorf("failed to send signal: %w", err)
	}
	return nil
}
