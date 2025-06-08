package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	stdlog "log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"go.uber.org/zap"

	"github.com/bifrostinc/code-sync-sidecar/log"
)

const (
	DefaultFilesDir = "/app-files"
)

func main() {
	// Use standard logger ONLY for errors *before* zap is initialized
	stdLogger := stdlog.New(os.Stderr, "[INIT_ERROR] ", stdlog.LstdFlags)

	// Read configuration from environment variables
	appID := os.Getenv("BIFROST_APP_ID")
	deploymentID := os.Getenv("BIFROST_DEPLOYMENT_ID")
	filesDir := os.Getenv("BIFROST_FILES_DIR")
	if filesDir == "" {
		filesDir = DefaultFilesDir
	}

	if appID == "" {
		stdLogger.Fatal("BIFROST_APP_ID environment variable is required")
	}
	if deploymentID == "" {
		stdLogger.Fatal("BIFROST_DEPLOYMENT_ID environment variable is required")
	}

	apiKey := os.Getenv("BIFROST_API_KEY")
	if apiKey == "" {
		stdLogger.Fatal("BIFROST_API_KEY environment variable is required")
	}

	apiURL := os.Getenv("BIFROST_API_URL")
	if apiURL == "" {
		stdLogger.Fatal("BIFROST_API_URL environment variable is required")
	}

	// Initialize the global logger
	initialFields := map[string]string{
		"appID":        appID,
		"deploymentID": deploymentID,
	}
	log.Init("code-sync-sidecar", initialFields)
	defer log.Sync() // Ensure logs are flushed on exit

	log.Info("Starting code-sync-sidecar",
		zap.String("filesDir", filesDir),
		zap.String("apiURL", apiURL),
	)

	// Create the sidecar and launcher directories with very open permissions so can be accessed by the app and sidecar.
	if err := os.MkdirAll(getSidecarDir(filesDir), 0777); err != nil {
		log.Fatal("Failed to create sidecar directory", zap.Error(err), zap.String("path", getSidecarDir(filesDir)))
	}
	if err := os.MkdirAll(getLauncherDir(filesDir), 0777); err != nil {
		log.Fatal("Failed to create launcher directory", zap.Error(err), zap.String("path", getLauncherDir(filesDir)))
	}
	if err := os.Chmod(getSidecarDir(filesDir), 0777); err != nil {
		log.Warn("Failed to change sidecar directory permissions", zap.Error(err), zap.String("path", getSidecarDir(filesDir)))
	}
	if err := os.Chmod(getLauncherDir(filesDir), 0777); err != nil {
		log.Warn("Failed to change launcher directory permissions", zap.Error(err), zap.String("path", getLauncherDir(filesDir)))
	}

	log.Info("Created sidecar and launcher directories")
	if err := copyBinaries(filesDir); err != nil {
		log.Fatal("Failed to copy binaries", zap.Error(err))
	}

	// Fetch and write database environment variables
	if err := writeDatabaseEnvFile(apiURL, apiKey, deploymentID, filesDir); err != nil {
		log.Warn("Failed to write database environment file", zap.Error(err))
		// Don't fail - let the app start without database URLs
	}

	// Create a context that will be canceled on SIGTERM/SIGINT
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		sig := <-sigChan
		log.Info("Received signal, initiating shutdown", zap.String("signal", sig.String()))
		cancel()
	}()

	rsync, err := NewFileSyncer(
		ctx,
		apiURL,
		apiKey,
		appID,
		deploymentID,
		filesDir,
	)
	if err != nil {
		log.Fatal("Failed to create file syncer", zap.Error(err))
	}
	// Wait for context cancellation (signal or other shutdown reason)
	<-ctx.Done()
	log.Info("Shutdown context cancelled, stopping components")
	rsync.Stop()

	log.Info("Shutdown complete")
}

// copyFile now uses the global logger
func copyFile(src, dst string) error {
	log.Info("Copying file", zap.String("source", src), zap.String("destination", dst))
	srcFile, err := os.Open(src)
	if err != nil {
		return fmt.Errorf("failed to open source file %s: %w", src, err)
	}
	defer srcFile.Close()

	dstFile, err := os.Create(dst)
	if err != nil {
		return fmt.Errorf("failed to create destination file %s: %w", dst, err)
	}
	defer dstFile.Close()

	bytesCopied, err := io.Copy(dstFile, srcFile)
	if err != nil {
		return fmt.Errorf("failed to copy data from %s to %s: %w", src, dst, err)
	}

	// Make the destination file executable (0777)
	if err := os.Chmod(dst, 0777); err != nil {
		log.Warn("Failed to set executable permission", zap.String("file", dst), zap.Error(err))
	}

	log.Info("Successfully copied file",
		zap.String("source", src),
		zap.String("destination", dst),
		zap.Int64("bytesCopied", bytesCopied),
	)
	return nil
}

func getSidecarDir(filesDir string) string {
	return filepath.Join(filesDir, ".sidecar")
}

func getLauncherDir(filesDir string) string {
	return filepath.Join(filesDir, ".launcher")
}

var filesToCopy = []string{
	"rsync_amd64",
	"rsync_arm64",
	"rsync-launcher.sh",
}

// copyBinaries now uses the global logger
func copyBinaries(filesDir string) error {
	log.Info("Setting up binaries", zap.String("targetDir", filesDir))
	binDir := getSidecarDir(filesDir)
	if err := os.MkdirAll(binDir, 0777); err != nil {
		return fmt.Errorf("failed to ensure sidecar directory exists %s: %w", binDir, err)
	}

	for _, file := range filesToCopy {
		src := filepath.Join("/app/bin", file)
		dst := filepath.Join(binDir, file)
		if err := os.MkdirAll(filepath.Dir(dst), 0777); err != nil {
			return fmt.Errorf("failed to create directory %s for binary %s: %w", filepath.Dir(dst), file, err)
		}
		if err := copyFile(src, dst); err != nil {
			return fmt.Errorf("failed to copy binary %s: %w", file, err)
		}
	}
	log.Info("Successfully set up binaries", zap.String("targetDir", filesDir))
	return nil
}

// DatabaseEnvVar represents a database environment variable
type DatabaseEnvVar struct {
	EnvVarName     string `json:"env_var_name"`
	ConnectionURI  string `json:"connection_uri"`
}

// writeDatabaseEnvFile fetches database connection URIs from the API and writes them to an env file
func writeDatabaseEnvFile(apiURL, apiKey, deploymentID, filesDir string) error {
	log.Info("Fetching database environment variables", 
		zap.String("deploymentID", deploymentID),
		zap.String("apiURL", apiURL))

	// Build the API endpoint URL
	url := fmt.Sprintf("%s/api/v1/deployments/%s/database-env-vars", apiURL, deploymentID)
	
	// Create HTTP client with timeout
	client := &http.Client{
		Timeout: 10 * time.Second,
	}
	
	// Create request with API key header
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("X-Api-Key", apiKey)
	
	// Make the request
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to fetch database env vars: %w", err)
	}
	defer resp.Body.Close()
	
	// Check response status
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("API request failed with status %d: %s", resp.StatusCode, string(body))
	}
	
	// Parse response
	var envVars []DatabaseEnvVar
	if err := json.NewDecoder(resp.Body).Decode(&envVars); err != nil {
		return fmt.Errorf("failed to decode response: %w", err)
	}
	
	// If no databases, don't create the file
	if len(envVars) == 0 {
		log.Info("No database environment variables to inject")
		return nil
	}
	
	// Write to env.sh file
	envFile := filepath.Join(getSidecarDir(filesDir), "env.sh")
	f, err := os.Create(envFile)
	if err != nil {
		return fmt.Errorf("failed to create env file: %w", err)
	}
	defer f.Close()
	
	// Write each environment variable
	for _, envVar := range envVars {
		if _, err := fmt.Fprintf(f, "export %s=\"%s\"\n", envVar.EnvVarName, envVar.ConnectionURI); err != nil {
			return fmt.Errorf("failed to write env var %s: %w", envVar.EnvVarName, err)
		}
		log.Info("Added database environment variable", 
			zap.String("envVar", envVar.EnvVarName),
			zap.String("envFile", envFile))
	}
	
	// Make file readable by all
	if err := os.Chmod(envFile, 0644); err != nil {
		log.Warn("Failed to set env file permissions", zap.Error(err))
	}
	
	log.Info("Successfully wrote database environment variables", 
		zap.String("envFile", envFile),
		zap.Int("count", len(envVars)))
	
	return nil
}
