package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"go.uber.org/zap"

	"github.com/bifrostinc/code-sync-sidecar/log"
)

// EnvironmentManager manages environment variables from push messages,
// syncing them to a .env file for use by the launcher script.
type EnvironmentManager struct {
	envFilePath string
}

// NewEnvironmentManager creates a new environment manager that will write
// environment variables to the specified directory.
func NewEnvironmentManager(targetSyncDir string) *EnvironmentManager {
	sidecarDir := getSidecarDir(targetSyncDir)
	envFilePath := filepath.Join(sidecarDir, ".env")
	
	return &EnvironmentManager{
		envFilePath: envFilePath,
	}
}

// UpdateFromPush updates the environment variables from a push message.
// This acts as a PUT operation - it completely replaces all environment variables
// with the ones provided in the push message.
func (em *EnvironmentManager) UpdateFromPush(envVariables map[string]string) error {
	if envVariables == nil {
		envVariables = make(map[string]string)
	}

	log.Info("Updating environment variables from push", 
		zap.Int("numVariables", len(envVariables)),
		zap.String("envFilePath", em.envFilePath))

	// Ensure the sidecar directory exists
	if err := os.MkdirAll(filepath.Dir(em.envFilePath), 0777); err != nil {
		return fmt.Errorf("failed to create sidecar directory: %w", err)
	}

	// Build the .env file content
	var lines []string
	
	// Sort keys for consistent output and easier testing
	var keys []string
	for key := range envVariables {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	for _, key := range keys {
		value := envVariables[key]
		// Escape values that contain special characters by wrapping in quotes
		escapedValue := escapeEnvValue(value)
		line := fmt.Sprintf("export %s=%s", key, escapedValue)
		lines = append(lines, line)
	}

	content := strings.Join(lines, "\n")
	if len(lines) > 0 {
		content += "\n" // Add trailing newline
	}

	// Write the .env file atomically using a temporary file
	tempFile := em.envFilePath + ".tmp"
	if err := os.WriteFile(tempFile, []byte(content), 0644); err != nil {
		return fmt.Errorf("failed to write temporary env file: %w", err)
	}

	if err := os.Rename(tempFile, em.envFilePath); err != nil {
		os.Remove(tempFile) // Clean up on failure
		return fmt.Errorf("failed to replace env file: %w", err)
	}

	log.Info("Successfully updated environment file", 
		zap.String("path", em.envFilePath),
		zap.Int("variables", len(envVariables)))

	return nil
}

// GetEnvFilePath returns the path to the .env file.
func (em *EnvironmentManager) GetEnvFilePath() string {
	return em.envFilePath
}

// escapeEnvValue escapes environment variable values for shell consumption.
// It wraps values in single quotes if they contain special characters.
func escapeEnvValue(value string) string {
	// If the value is empty, return empty quotes
	if value == "" {
		return "''"
	}

	// Check if the value needs escaping (contains spaces, quotes, or special chars)
	needsEscaping := false
	for _, char := range value {
		if char == ' ' || char == '\t' || char == '\n' || char == '\r' || 
		   char == '"' || char == '\'' || char == '\\' || char == '$' ||
		   char == '`' || char == '|' || char == '&' || char == ';' ||
		   char == '(' || char == ')' || char == '<' || char == '>' {
			needsEscaping = true
			break
		}
	}

	if !needsEscaping {
		return value
	}

	// Use single quotes and escape any single quotes in the value
	escaped := strings.ReplaceAll(value, "'", "'\"'\"'")
	return "'" + escaped + "'"
}