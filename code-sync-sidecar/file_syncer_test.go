package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/proto"

	"github.com/bifrostinc/code-sync-sidecar/pb"
)

// mockProcess implements ProcessSignaler for testing
type mockProcess struct {
	signalCalls []syscall.Signal
	signalErr   error
}

func (m *mockProcess) Signal(sig syscall.Signal) error {
	if m.signalErr != nil {
		return m.signalErr
	}
	m.signalCalls = append(m.signalCalls, sig)
	return nil
}

// mockProcessFinder implements ProcessFinder for testing
type mockProcessFinder struct {
	processes map[int]*mockProcess
	findErr   error
}

func (m *mockProcessFinder) FindProcess(pid int) (ProcessSignaler, error) {
	if m.findErr != nil {
		return nil, m.findErr
	}
	proc, ok := m.processes[pid]
	if !ok {
		proc = &mockProcess{}
		m.processes[pid] = proc
	}
	return proc, nil
}

// Test helper process for mocking exec.Command
func helperCommandContext(ctx context.Context, command string, args ...string) *exec.Cmd {
	cs := []string{"-test.run=TestHelperProcess", "--", command}
	cs = append(cs, args...)
	cmd := exec.CommandContext(ctx, os.Args[0], cs...) // Use CommandContext
	cmd.Env = []string{"GO_WANT_HELPER_PROCESS=1"}
	// Propagate other environment variables needed by the helper
	cmd.Env = append(cmd.Env, os.Environ()...)
	return cmd
}

var upgrader = websocket.Upgrader{}

// mockWebsocketServer captures messages sent to the websocket
type mockWebsocketServer struct {
	messages chan []byte
	conn     *websocket.Conn
}

func (m *mockWebsocketServer) handler(w http.ResponseWriter, r *http.Request) {
	// Upgrade our raw HTTP connection to a websocket based one
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Print("Error during connection upgradation:", err)
		return
	}
	m.conn = conn
	defer conn.Close()

	// Read messages sent to the websocket
	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			break // Connection closed or error
		}
		m.messages <- message
	}
}

func newMockWebsocket(t *testing.T) (*websocket.Conn, *mockWebsocketServer) {
	mockServer := &mockWebsocketServer{messages: make(chan []byte, 100)}
	s := httptest.NewServer(http.HandlerFunc(mockServer.handler))
	defer s.Close()
	wsURL := "ws" + strings.TrimPrefix(s.URL, "http")
	c, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Error(err)
	}
	return c, mockServer
}

// TestHelperProcess isn't a real test. It's used as a helper process
// for tests that need to mock external commands like rsync.
func TestHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_HELPER_PROCESS") != "1" {
		return
	}
	defer os.Exit(0)

	args := os.Args
	for len(args) > 0 {
		if args[0] == "--" {
			args = args[1:]
			break
		}
		args = args[1:]
	}
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, "HelperProcess: No command\n")
		os.Exit(2)
	}

	cmd, args := args[0], args[1:]
	// Use basename for matching as the test might generate a path inside .sidecar
	cmdBase := filepath.Base(cmd)

	// Simulate rsync behavior
	if cmdBase == "rsync" {
		// Check for a specific argument or environment variable to trigger failure
		if os.Getenv("HELPER_RSYNC_FAIL") == "1" {
			fmt.Fprintf(os.Stderr, "rsync simulation error output\n")
			os.Exit(1) // Simulate rsync error exit code
		}
		// Check if the expected batch file argument exists
		batchFileArgPrefix := "--read-batch="
		foundBatchArg := false
		expectedBatchFile := os.Getenv("HELPER_EXPECTED_BATCH_FILE")
		for _, arg := range args {
			if strings.HasPrefix(arg, batchFileArgPrefix) {
				if expectedBatchFile == "" || arg == batchFileArgPrefix+expectedBatchFile {
					foundBatchArg = true
					break
				} else {
					fmt.Fprintf(os.Stderr, "HelperProcess: rsync received wrong batch file arg: %s, expected prefix %s\n", arg, batchFileArgPrefix+expectedBatchFile)
					os.Exit(3) // Indicate wrong arguments
				}
			}
		}
		if !foundBatchArg && expectedBatchFile != "" {
			fmt.Fprintf(os.Stderr, "HelperProcess: rsync did not receive expected batch file arg starting with %s\n", batchFileArgPrefix+expectedBatchFile)
			os.Exit(3)
		}

		fmt.Fprintf(os.Stdout, "rsync simulation success output\n")
		os.Exit(0) // Simulate rsync success
	} else {
		fmt.Fprintf(os.Stderr, "HelperProcess: Unknown command %s\n", cmdBase)
		os.Exit(2)
	}
}

func TestNewFileSyncer(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "rsync_watcher_test_new")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel() // Ensure context is cancelled eventually

	rw, err := NewFileSyncer(ctx, "http://localhost:8080", "test-key", "app1", "deployment1", tmpDir)
	require.NoError(t, err)
	require.NotNil(t, rw)

	assert.Equal(t, "http://localhost:8080", rw.apiURL)
	assert.Equal(t, "test-key", rw.apiKey)
	assert.Equal(t, "app1", rw.appID)
	assert.Equal(t, "deployment1", rw.deploymentID)
	assert.Equal(t, tmpDir, rw.targetSyncDir)
	assert.NotNil(t, rw.done)
	assert.NotNil(t, rw.processFinder)
	assert.Nil(t, rw.conn) // Connection not established yet

	// Allow some time for the goroutine to potentially start and then stop it
	time.Sleep(50 * time.Millisecond)
	rw.Stop()

	// Check if done channel is closed
	select {
	case _, ok := <-rw.done:
		assert.False(t, ok, "Done channel should be closed after Stop()")
	case <-time.After(1 * time.Second):
		t.Fatal("Timed out waiting for done channel to close")
	}
}

func TestFileSyncer_Stop(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "rsync_watcher_test_stop")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Use a real WebSocket server to test the close message sending part
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upgrader := websocket.Upgrader{}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return // Cannot upgrade
		}
		defer conn.Close()
		// Keep connection open until client closes or sends close message
		for {
			if _, _, err := conn.NextReader(); err != nil {
				break // Connection closed
			}
		}
	}))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")

	// Unused context removed
	// ctx, cancel := context.WithCancel(context.Background())

	rw := &FileSyncer{
		apiURL:        "http://localhost:8080", // Not used directly in Stop, but needed for New
		apiKey:        "test-key",
		appID:         "app1",
		deploymentID:  "deployment1",
		targetSyncDir: tmpDir,
		done:          make(chan struct{}),
		processFinder: &mockProcessFinder{},
	}

	// Manually connect for this test
	headers := http.Header{"X-Api-Key": []string{rw.apiKey}}
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, headers)
	require.NoError(t, err)
	rw.conn = conn // Assign the connection

	// Run Stop in a goroutine because the server interaction might block briefly
	stopDone := make(chan struct{})
	go func() {
		rw.Stop()
		close(stopDone)
	}()

	// Wait for Stop to complete or timeout
	select {
	case <-stopDone:
		// Stop completed
	case <-time.After(2 * time.Second): // Increased timeout
		t.Fatal("Timed out waiting for Stop() to complete")
	}

	// Check if done channel is closed
	select {
	case _, ok := <-rw.done:
		assert.False(t, ok, "Done channel should be closed")
	default:
		t.Error("Done channel was not closed")
	}
}

func TestBuildWebSocketURL(t *testing.T) {
	tests := []struct {
		name     string
		apiURL   string
		expected string
	}{
		{
			name:     "http url",
			apiURL:   "http://localhost:8080",
			expected: "ws://localhost:8080/api/v1/push/sidecar/app1/deployment1",
		},
		{
			name:     "https url",
			apiURL:   "https://codesync.example.com",
			expected: "wss://codesync.example.com/api/v1/push/sidecar/app1/deployment1",
		},
		{
			name:     "http url with path",
			apiURL:   "http://localhost:8080/basepath", // Base path should be ignored
			expected: "ws://localhost:8080/api/v1/push/sidecar/app1/deployment1",
		},
		{
			name:     "https url with port",
			apiURL:   "https://codesync.example.com:4443",
			expected: "wss://codesync.example.com:4443/api/v1/push/sidecar/app1/deployment1",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rw := &FileSyncer{
				apiURL:       tt.apiURL,
				appID:        "app1",
				deploymentID: "deployment1",
			}
			actual := rw.buildWebSocketURL()
			assert.Equal(t, tt.expected, actual)
		})
	}
}

// TestApplyPushMessage tests the push message handling logic.
// It uses a helper process to mock the actual rsync command execution.
func TestApplyPushMessage(t *testing.T) {
	tmpDir, err := os.MkdirTemp("", "rsync_apply_test")
	require.NoError(t, err)
	defer os.RemoveAll(tmpDir)

	// Need a valid PID file for sendSignalToLauncher to potentially work
	sidecarDir := getSidecarDir(tmpDir)
	require.NoError(t, os.MkdirAll(sidecarDir, 0777))
	pidFilePath := filepath.Join(sidecarDir, "launcher.pid")
	err = os.WriteFile(pidFilePath, []byte("12345"), 0644)
	require.NoError(t, err)

	tests := []struct {
		name            string
		batchData       []byte
		envVars         map[string]string
		rsyncShouldFail bool   // Tells the helper process to exit with non-zero status
		mockFinderErr   error  // Error for mockProcessFinder.FindProcess
		mockSignalErr   error  // Error for mockProcess.Signal
		expectSignal    bool   // Whether SIGHUP is expected
		expectResponse  bool   // Whether a message is expected to be sent to the websocket
		expectedErr     string // Expected error string from handlePushRequest, empty for success
	}{
		{
			name:           "empty batch data with env vars",
			batchData:      []byte{},
			envVars:        map[string]string{"TEST_VAR": "test_value", "DB_URL": "postgres://localhost/test"},
			expectSignal:   true,  // Signal sent even for env-only updates
			expectResponse: true,  // Message sent for successful env update
			expectedErr:    "",
		},
		{
			name:           "env vars only, no batch data",
			batchData:      nil,
			envVars:        map[string]string{"API_KEY": "secret123"},
			expectSignal:   true,
			expectResponse: true,
			expectedErr:    "",
		},
		{
			name:           "valid batch data with env vars, rsync success",
			batchData:      []byte("fake-rsync-batch-data"),
			envVars:        map[string]string{"BUILD_ID": "12345"},
			expectSignal:   true,
			expectResponse: true,
			expectedErr:    "",
		},
		{
			name:            "valid batch data with env vars, rsync command fails",
			batchData:       []byte("trigger-fail"),
			envVars:         map[string]string{"TEST_VAR": "value"},
			rsyncShouldFail: true,
			expectSignal:    false, // No signal if rsync fails
			expectResponse:  true,  // Message is sent for failed rsync
			expectedErr:     "rsync command failed: exit status 1",
		},
		{
			name:           "rsync success, find process fails",
			batchData:      []byte("find-fail-data"),
			envVars:        map[string]string{"VAR": "value"},
			mockFinderErr:  os.ErrNotExist,
			expectSignal:   false,                 // Signal sending fails
			expectResponse: true,                  // Message is sent for failed signal
			expectedErr:    "file does not exist", // handlePushRequest succeeds until signal fails
		},
		{
			name:           "rsync success, signal process fails",
			batchData:      []byte("signal-fail-data"),
			envVars:        map[string]string{"VAR": "value"},
			mockSignalErr:  os.ErrPermission,
			expectSignal:   true, // Attempted, but failed
			expectResponse: true, // Message is sent for failed signal
			expectedErr:    "permission denied",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			pushID := "test-push-id"
			testSpecificDir := tmpDir

			// Setup mock process finder for this test case
			mockFinder := &mockProcessFinder{
				processes: make(map[int]*mockProcess),
				findErr:   tt.mockFinderErr,
			}
			if tt.mockSignalErr != nil {
				mockFinder.processes[12345] = &mockProcess{signalErr: tt.mockSignalErr}
			} else {
				mockFinder.processes[12345] = &mockProcess{}
			}

			launcherDir := getLauncherDir(testSpecificDir)
			require.NoError(t, os.MkdirAll(launcherDir, 0777))
			err = os.WriteFile(filepath.Join(launcherDir, "launcher.pid"), []byte("12345"), 0644)
			require.NoError(t, err)

			conn, mockServer := newMockWebsocket(t)
			rw := &FileSyncer{
				targetSyncDir: testSpecificDir,
				processFinder: mockFinder,
				conn:          conn,
				envManager:    NewEnvironmentManager(testSpecificDir),
			}
			defer conn.Close()

			// Setup environment for helper process
			if tt.rsyncShouldFail {
				os.Setenv("HELPER_RSYNC_FAIL", "1")
			} else {
				os.Unsetenv("HELPER_RSYNC_FAIL") // Ensure it's not set from previous tests
			}

			// Pass the expected temp batch file name to the helper for verification
			// We can't know the exact random name, so we check the prefix/existence.
			// For more robust check, we could parse args better in helper.
			// Let's skip HELPER_EXPECTED_BATCH_FILE for now.
			os.Unsetenv("HELPER_EXPECTED_BATCH_FILE")

			// Override execCommand for the duration of this test run
			// This is the key part for using the helper process.
			originalExecCommand := execCommand
			execCommand = helperCommandContext
			defer func() { execCommand = originalExecCommand }()

			// Run the function under test
			err := rw.handlePushRequest(&pb.PushMessage{
				PushId: pushID, 
				BatchFile: tt.batchData,
				EnvironmentVariables: tt.envVars,
			})

			// Check error
			if tt.expectedErr != "" {
				require.Error(t, err)
				assert.Contains(t, err.Error(), tt.expectedErr)
			} else {
				assert.NoError(t, err)
			}

			// Check if signal was sent (by checking the mock)
			proc, found := mockFinder.processes[12345]
			if tt.expectSignal {
				assert.True(t, found, "Process 12345 should have been looked up")
				if found && tt.mockSignalErr == nil { // Only check calls if signal wasn't mocked to fail
					assert.Contains(t, proc.signalCalls, syscall.SIGHUP, "SIGHUP signal should have been sent")
				}
			} else {
				// If no signal was expected, ensure no SIGHUP was recorded (unless lookup failed)
				if found && tt.mockFinderErr == nil && tt.mockSignalErr == nil {
					assert.NotContains(t, proc.signalCalls, syscall.SIGHUP, "SIGHUP signal should NOT have been sent")
				}
			}

			// Allow some time for websocket message to be sent and received
			time.Sleep(50 * time.Millisecond)

			if tt.expectResponse {
				var wsMessage pb.WebsocketMessage
			waitLoop:
				for {
					select {
					case message := <-mockServer.messages:
						err2 := proto.Unmarshal(message, &wsMessage)
						require.NoError(t, err2, "Failed to unmarshal websocket message")
						break waitLoop
					case <-time.After(1 * time.Second):
						t.Fatal("Timed out waiting for websocket message")
					}
				}

				// Verify it's a push response
				assert.Equal(t, pb.WebsocketMessage_PUSH_RESPONSE, wsMessage.MessageType, "Message type should be PUSH_RESPONSE")
				pushResponse := wsMessage.GetPushResponse()
				require.NotNil(t, pushResponse, "Push response should not be nil")

				// Verify the push ID matches
				assert.Equal(t, pushID, pushResponse.GetPushId(), "Push ID should match")
				if tt.expectedErr != "" {
					assert.Equal(t, pb.PushResponse_FAILED, pushResponse.GetStatus(), "Status should be FAILED for error cases")
					assert.NotEmpty(t, pushResponse.GetErrorMessage(), "Error message should be provided for failed cases")
					assert.Contains(t, pushResponse.GetErrorMessage(), tt.expectedErr, "Error message should contain expected error")
				} else {
					assert.Equal(t, pb.PushResponse_COMPLETED, pushResponse.GetStatus(), "Status should be COMPLETED for successful cases")
					assert.Empty(t, pushResponse.GetErrorMessage(), "Error message should be empty for successful cases")
				}
			}

			// Verify environment variables were written correctly
			if len(tt.envVars) > 0 {
				envFilePath := rw.envManager.GetEnvFilePath()
				envContent, err := os.ReadFile(envFilePath)
				require.NoError(t, err, "Should be able to read environment file")
				
				envStr := string(envContent)
				for key, value := range tt.envVars {
					expectedLine := fmt.Sprintf("export %s=%s", key, value)
					// For simple values without special characters, they won't be quoted
					if !strings.Contains(value, " ") && !strings.Contains(value, "'") && !strings.Contains(value, "\"") {
						assert.Contains(t, envStr, expectedLine, "Environment file should contain expected variable")
					} else {
						// For complex values, just check the key is present
						assert.Contains(t, envStr, fmt.Sprintf("export %s=", key), "Environment file should contain variable key")
					}
				}
			}

			// Clean up environment variable for next test
			os.Unsetenv("HELPER_RSYNC_FAIL")
			os.Unsetenv("HELPER_EXPECTED_BATCH_FILE")
		})
	}
}
