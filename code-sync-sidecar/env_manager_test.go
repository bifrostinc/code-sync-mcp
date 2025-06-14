package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNewEnvironmentManager(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "env_manager_test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	em := NewEnvironmentManager(tmpDir)
	require.NotNil(t, em)

	expectedPath := filepath.Join(getSidecarDir(tmpDir), ".env")
	assert.Equal(t, expectedPath, em.GetEnvFilePath())
}

func TestEnvironmentManager_UpdateFromPush(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "env_manager_update_test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	em := NewEnvironmentManager(tmpDir)

	tests := []struct {
		name           string
		envVars        map[string]string
		expectedLines  []string
		expectedError  bool
	}{
		{
			name:    "nil environment variables",
			envVars: nil,
			expectedLines: []string{},
			expectedError: false,
		},
		{
			name:    "empty environment variables",
			envVars: map[string]string{},
			expectedLines: []string{},
			expectedError: false,
		},
		{
			name: "simple environment variables",
			envVars: map[string]string{
				"API_KEY": "secret123",
				"DB_URL":  "postgres://localhost/test",
			},
			expectedLines: []string{
				"export API_KEY=secret123",
				"export DB_URL=postgres://localhost/test",
			},
			expectedError: false,
		},
		{
			name: "environment variables with special characters",
			envVars: map[string]string{
				"COMPLEX_VAR": "value with spaces",
				"QUOTED_VAR":  "value'with'quotes",
				"DOLLAR_VAR":  "value$with$dollars",
			},
			expectedLines: []string{
				"export COMPLEX_VAR='value with spaces'",
				"export QUOTED_VAR='value'\"'\"'with'\"'\"'quotes'",
				"export DOLLAR_VAR='value$with$dollars'",
			},
			expectedError: false,
		},
		{
			name: "empty value",
			envVars: map[string]string{
				"EMPTY_VAR": "",
			},
			expectedLines: []string{
				"export EMPTY_VAR=''",
			},
			expectedError: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := em.UpdateFromPush(tt.envVars)
			
			if tt.expectedError {
				require.Error(t, err)
				return
			}
			
			require.NoError(t, err)

			// Read the generated file
			content, err := os.ReadFile(em.GetEnvFilePath())
			require.NoError(t, err)

			contentStr := string(content)
			lines := strings.Split(strings.TrimSpace(contentStr), "\n")
			
			// If no environment variables, file should be empty
			if len(tt.envVars) == 0 {
				assert.Empty(t, strings.TrimSpace(contentStr), "File should be empty for no env vars")
				return
			}

			// Filter out empty lines
			var actualLines []string
			for _, line := range lines {
				if strings.TrimSpace(line) != "" {
					actualLines = append(actualLines, line)
				}
			}

			assert.Len(t, actualLines, len(tt.expectedLines), "Number of lines should match")
			
			// Check that all expected lines are present (order might vary due to map iteration)
			for _, expectedLine := range tt.expectedLines {
				found := false
				for _, actualLine := range actualLines {
					if actualLine == expectedLine {
						found = true
						break
					}
				}
				assert.True(t, found, "Expected line '%s' not found in output: %v", expectedLine, actualLines)
			}

			// Verify file ends with newline if there are variables
			if len(tt.envVars) > 0 {
				assert.True(t, strings.HasSuffix(contentStr, "\n"), "File should end with newline")
			}
		})
	}
}

func TestEnvironmentManager_UpdateFromPush_Atomic(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "env_manager_atomic_test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	em := NewEnvironmentManager(tmpDir)

	// First update
	envVars1 := map[string]string{
		"VAR1": "value1",
		"VAR2": "value2",
	}
	err = em.UpdateFromPush(envVars1)
	require.NoError(t, err)

	// Verify first update
	content, err := os.ReadFile(em.GetEnvFilePath())
	require.NoError(t, err)
	contentStr := string(content)
	assert.Contains(t, contentStr, "export VAR1=value1")
	assert.Contains(t, contentStr, "export VAR2=value2")

	// Second update - should completely replace
	envVars2 := map[string]string{
		"VAR3": "value3",
	}
	err = em.UpdateFromPush(envVars2)
	require.NoError(t, err)

	// Verify second update completely replaced the first
	content, err = os.ReadFile(em.GetEnvFilePath())
	require.NoError(t, err)
	contentStr = string(content)
	assert.Contains(t, contentStr, "export VAR3=value3")
	assert.NotContains(t, contentStr, "VAR1", "Old variables should be removed")
	assert.NotContains(t, contentStr, "VAR2", "Old variables should be removed")
}

func TestEscapeEnvValue(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "simple value",
			input:    "simple",
			expected: "simple",
		},
		{
			name:     "empty value",
			input:    "",
			expected: "''",
		},
		{
			name:     "value with spaces",
			input:    "value with spaces",
			expected: "'value with spaces'",
		},
		{
			name:     "value with single quotes",
			input:    "value'with'quotes",
			expected: "'value'\"'\"'with'\"'\"'quotes'",
		},
		{
			name:     "value with double quotes",
			input:    "value\"with\"quotes",
			expected: "'value\"with\"quotes'",
		},
		{
			name:     "value with dollar signs",
			input:    "value$with$dollars",
			expected: "'value$with$dollars'",
		},
		{
			name:     "value with backslashes",
			input:    "value\\with\\backslashes",
			expected: "'value\\with\\backslashes'",
		},
		{
			name:     "value with backticks",
			input:    "value`with`backticks",
			expected: "'value`with`backticks'",
		},
		{
			name:     "value with special shell characters",
			input:    "value|with&special;chars",
			expected: "'value|with&special;chars'",
		},
		{
			name:     "value with parentheses",
			input:    "value(with)parens",
			expected: "'value(with)parens'",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := escapeEnvValue(tt.input)
			assert.Equal(t, tt.expected, result)
		})
	}
}

func TestEnvironmentManager_DirectoryCreation(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "env_manager_dir_test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Use a subdirectory that doesn't exist yet
	targetDir := filepath.Join(tmpDir, "non-existent", "nested")
	em := NewEnvironmentManager(targetDir)

	envVars := map[string]string{
		"TEST_VAR": "test_value",
	}

	err = em.UpdateFromPush(envVars)
	require.NoError(t, err)

	// Verify the directories were created
	sidecarDir := getSidecarDir(targetDir)
	assert.DirExists(t, sidecarDir, "Sidecar directory should be created")

	// Verify the file was created with correct content
	content, err := os.ReadFile(em.GetEnvFilePath())
	require.NoError(t, err)
	assert.Contains(t, string(content), "export TEST_VAR=test_value")
}