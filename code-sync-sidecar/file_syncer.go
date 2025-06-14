package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/gorilla/websocket"
	"go.uber.org/zap"
	"google.golang.org/protobuf/proto"

	"github.com/bifrostinc/code-sync-sidecar/log"
	"github.com/bifrostinc/code-sync-sidecar/pb"
)

// execCommand allows mocking exec.CommandContext in tests
var execCommand = exec.CommandContext

const rsyncPath = "/app/bin/rsync"

// FileSyncer handles syncing files via rsync triggered by WebSocket messages.
type FileSyncer struct {
	apiURL        string
	apiKey        string
	appID         string
	deploymentID  string
	targetSyncDir string
	conn          *websocket.Conn
	done          chan struct{}
	processFinder ProcessFinder
}

// NewFileSyncer creates and starts a new FileSyncer.
func NewFileSyncer(
	ctx context.Context,
	apiURL string,
	apiKey string,
	appID string,
	deploymentID string,
	targetSyncDir string,
) (*FileSyncer, error) {
	rw := &FileSyncer{
		apiURL:        apiURL,
		apiKey:        apiKey,
		appID:         appID,
		deploymentID:  deploymentID,
		targetSyncDir: targetSyncDir,
		done:          make(chan struct{}),
		processFinder: &DefaultProcessFinder{},
	}

	go rw.run(ctx)

	// Logging about start is now done in main.go
	return rw, nil
}

// Stop gracefully shuts down the FileSyncer.
func (rw *FileSyncer) Stop() {
	log.Info("Stopping file syncer...")
	close(rw.done)
	if rw.conn != nil {
		// Cleanly close the WebSocket connection
		err := rw.conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
		if err != nil {
			log.Warn("Error sending WebSocket close message", zap.Error(err))
		}
		rw.conn.Close()
	}
	log.Info("File syncer stopped.")
}

// run is the main loop for the FileSyncer.
func (rw *FileSyncer) run(ctx context.Context) {
	wsURL := rw.buildWebSocketURL()
	headers := http.Header{"X-Api-Key": []string{rw.apiKey}}

	for {
		select {
		case <-ctx.Done():
			log.Info("Context cancelled, shutting down.")
			rw.Stop() // Ensure Stop is called on context cancellation
			return
		case <-rw.done:
			log.Info("Stop signal received, shutting down.")
			return
		default:
			conn, resp, err := websocket.DefaultDialer.Dial(wsURL, headers)
			if err != nil {
				var respStatusCode int
				if resp != nil {
					respStatusCode = resp.StatusCode
				}
				log.Warn("Failed to connect to WebSocket",
					zap.String("url", wsURL),
					zap.Error(err),
					zap.Int("httpStatus", respStatusCode),
				)
				log.Info("Retrying WebSocket connection in 5 seconds...")
				time.Sleep(5 * time.Second)
				continue // Retry connection
			}
			// Close the response body explicitly if it's not nil
			if resp != nil && resp.Body != nil {
				resp.Body.Close()
			}

			rw.conn = conn
			log.Info("Connected to Code Sync proxy", zap.String("url", wsURL))

			// Connection successful, start message loop
			err = rw.messageLoop(ctx)
			if err != nil {
				log.Warn("WebSocket message loop ended", zap.Error(err))
			}
			// Close connection before retry or shutdown
			rw.conn.Close()
			rw.conn = nil

			// Check if we should exit or retry
			select {
			case <-ctx.Done():
				log.Info("Context cancelled after connection loss.")
				rw.Stop()
				return
			case <-rw.done:
				log.Info("Stop signal received after connection loss.")
				return
			default:
				log.Info("Connection lost. Retrying in 5 seconds...")
				time.Sleep(5 * time.Second)
			}
		}
	}
}

// messageLoop reads messages from the WebSocket connection.
func (rw *FileSyncer) messageLoop(ctx context.Context) error {
	for {
		select {
		case <-ctx.Done():
			return fmt.Errorf("context cancelled during message loop")
		case <-rw.done:
			return fmt.Errorf("stop signal received during message loop")
		default:
			// Set a read deadline to avoid blocking indefinitely if connection hangs
			// Using a slightly longer timeout to reduce noise from temporary network issues
			readTimeout := 90 * time.Second
			if err := rw.conn.SetReadDeadline(time.Now().Add(readTimeout)); err != nil {
				log.Warn("Failed to set read deadline", zap.Error(err))
			}

			messageType, message, err := rw.conn.ReadMessage()
			if err != nil {
				rw.conn.SetReadDeadline(time.Time{})

				if websocket.IsUnexpectedCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
					return fmt.Errorf("unexpected WebSocket close error: %w", err)
				}
				if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
					log.Warn("WebSocket read timeout", zap.Duration("timeout", readTimeout))
					return fmt.Errorf("read timeout: %w", err)
				}
				return fmt.Errorf("WebSocket read error: %w", err)
			}

			rw.conn.SetReadDeadline(time.Time{})

			if err := rw.handleMessage(messageType, message); err != nil {
				log.Error("Error handling message", zap.Error(err))
			}
		}
	}
}

func (rw *FileSyncer) handleMessage(messageType int, message []byte) error {
	switch messageType {
	case websocket.BinaryMessage:
		log.Debug("Received binary message", zap.Int("sizeBytes", len(message)))

		// Unmarshal the message using protobuf
		var incomingMsg pb.WebsocketMessage
		err := proto.Unmarshal(message, &incomingMsg)
		if err != nil {
			return fmt.Errorf("failed to unmarshal websocket message: %w", err)
		}

		msgTypeStr := incomingMsg.MessageType.String()
		log.Info("Received message", zap.String("type", msgTypeStr))
		switch incomingMsg.MessageType {
		case pb.WebsocketMessage_PUSH_REQUEST:
			return rw.handlePushRequest(incomingMsg.GetPushMessage())
		default:
			return fmt.Errorf("received unexpected message type: %s", msgTypeStr)
		}
	case websocket.PingMessage:
		log.Debug("Received Ping, sending Pong")
		if err := rw.conn.WriteMessage(websocket.PongMessage, nil); err != nil {
			log.Warn("Failed to send pong", zap.Error(err))
			return fmt.Errorf("failed to send pong: %w", err)
		}
	case websocket.CloseMessage:
		log.Info("Received close message from server.")
		return fmt.Errorf("server initiated close")
	default:
		log.Warn("Received unhandled message type", zap.Int("type", messageType))
	}
	return nil
}

func (rw *FileSyncer) handlePushRequest(pushMsg *pb.PushMessage) error {
	if pushMsg == nil {
		return fmt.Errorf("received PUSH_REQUEST but push_message field is nil")
	}
	pushID := pushMsg.PushId
	batchData := pushMsg.BatchFile

	// Log database branch updates if present
	if len(pushMsg.DatabaseBranchUpdates) > 0 {
		log.Info("Received database branch updates",
			zap.String("pushID", pushID),
			zap.Int("updateCount", len(pushMsg.DatabaseBranchUpdates)))

		for i, update := range pushMsg.DatabaseBranchUpdates {
			log.Info("Database branch update",
				zap.Int("index", i),
				zap.String("databaseName", update.DatabaseName),
				zap.String("previousBranchId", update.PreviousBranchId),
				zap.String("newBranchId", update.NewBranchId),
				zap.Bool("branchCreated", update.BranchCreated),
				zap.String("parentBranchId", update.ParentBranchId))
		}

		// Process database branch updates
		if err := rw.processDatabaseBranchUpdates(pushMsg.DatabaseBranchUpdates); err != nil {
			log.Error("Failed to process database branch updates", zap.Error(err))
			// Don't fail the entire push for database updates, just log the error
			// This ensures backward compatibility
		}
	} else {
		log.Info("No database branch updates in push message", zap.String("pushID", pushID))
	}

	// Handle code changes if present
	if len(batchData) > 0 {
		// Apply the rsync batch
		if err := rw.applyRsyncBatch(batchData); err != nil {
			log.Error("Failed to apply rsync batch", zap.Error(err))
			// Send PushResponse with FAILED status
			rw.sendProtoMessage(buildPushResponse(pushID, pb.PushResponse_FAILED, fmt.Sprintf("Push application failed: %v", err)))
			return fmt.Errorf("push application failed: %w", err)
		}

		log.Info("Rsync batch applied successfully.")

		// Write pushID to a file for the launcher script, it will get used by the launcher script.
		launcherDir := getLauncherDir(rw.targetSyncDir)
		pushIDFilePath := filepath.Join(launcherDir, "push_id")

		// Ensure the launcher dir exists (should be created by the script, but double-check)
		if err := os.MkdirAll(launcherDir, 0777); err != nil {
			return fmt.Errorf("failed to ensure launcher directory exists: %w", err)
		}

		// Write the pushID to the file
		if err := os.WriteFile(pushIDFilePath, []byte(pushID), 0644); err != nil {
			return fmt.Errorf("failed to write pushID to file: %w", err)
		}
		log.Info("Successfully wrote pushID to file", zap.String("path", pushIDFilePath), zap.String("pushID", pushID))

		if err := sendSignalToLauncher(rw.targetSyncDir, rw.processFinder); err != nil {
			log.Error("Failed to send SIGHUP", zap.Error(err))
			rw.sendProtoMessage(buildPushResponse(pushID, pb.PushResponse_FAILED, fmt.Sprintf("Failed to send SIGHUP: %v", err)))
			return fmt.Errorf("failed to send SIGHUP: %w", err)
		}

		log.Info("SIGHUP sent successfully. Sending ACK to proxy.")
	} else {
		log.Info("No code changes to apply, database updates only.")
	}

	// Always send a success response, regardless of whether there were code changes
	rw.sendProtoMessage(buildPushResponse(pushID, pb.PushResponse_COMPLETED, ""))

	return nil
}

// processDatabaseBranchUpdates handles database branch updates by refreshing the env file
func (rw *FileSyncer) processDatabaseBranchUpdates(updates []*pb.DatabaseBranchUpdate) error {
	if len(updates) == 0 {
		return nil
	}

	log.Info("Processing database branch updates",
		zap.Int("count", len(updates)),
		zap.String("deploymentID", rw.deploymentID))

	// Call the API to get the latest database environment variables
	// This will include the updated branch connections
	if err := writeDatabaseEnvFile(rw.apiURL, rw.apiKey, rw.deploymentID, rw.targetSyncDir); err != nil {
		return fmt.Errorf("failed to refresh database env file: %w", err)
	}

	log.Info("Successfully refreshed database environment variables after branch update")

	// Send SIGHUP to notify the application about the database connection changes
	if err := sendSignalToLauncher(rw.targetSyncDir, rw.processFinder); err != nil {
		log.Error("Failed to send SIGHUP after database update", zap.Error(err))
		return fmt.Errorf("failed to send SIGHUP after database update: %w", err)
	}

	log.Info("SIGHUP sent successfully after database branch update")

	return nil
}

// applyRsyncBatch applies the received rsync batch data.
func (rw *FileSyncer) applyRsyncBatch(batchData []byte) error {
	if len(batchData) == 0 {
		log.Info("Received empty batch data. Nothing to apply.")
		return nil // Not an error, just nothing to do
	}

	sidecarDir := getSidecarDir(rw.targetSyncDir)
	if err := os.MkdirAll(sidecarDir, 0777); err != nil {
		return fmt.Errorf("failed to create sidecar directory %s: %w", sidecarDir, err)
	}

	// Write batch data to a temporary file inside the .sidecar directory
	tempBatchFile, err := os.CreateTemp(sidecarDir, "sync_batch_*.bin")
	if err != nil {
		return fmt.Errorf("failed to create temporary batch file in %s: %w", sidecarDir, err)
	}
	defer os.Remove(tempBatchFile.Name())

	bytesWritten, err := tempBatchFile.Write(batchData)
	if err != nil {
		tempBatchFile.Close()
		return fmt.Errorf("failed to write to temporary batch file %s: %w", tempBatchFile.Name(), err)
	}
	tempBatchPath := tempBatchFile.Name()
	err = tempBatchFile.Close()
	if err != nil {
		return fmt.Errorf("failed to close temporary batch file %s: %w", tempBatchPath, err)
	}

	log.Info("Saved received batch data",
		zap.String("path", tempBatchPath),
		zap.Int("sizeBytes", bytesWritten),
	)

	if err := os.MkdirAll(rw.targetSyncDir, 0755); err != nil {
		return fmt.Errorf("failed to create target sync directory %s: %w", rw.targetSyncDir, err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	rsyncCmd := execCommand(ctx,
		rsyncPath,
		"--archive",
		fmt.Sprintf("--read-batch=%s", tempBatchPath),
		fmt.Sprintf("%s/", rw.targetSyncDir),
	)

	log.Info("Running rsync command", zap.String("command", rsyncCmd.String()))
	startTime := time.Now()
	output, err := rsyncCmd.CombinedOutput()
	duration := time.Since(startTime)

	logFields := []zap.Field{
		zap.Duration("duration", duration),
		zap.String("output", string(output)),
	}

	if err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			log.Error("Rsync command timed out", append(logFields, zap.Error(err))...)
			return fmt.Errorf("rsync command timed out after %v: %w", duration, err)
		}
		log.Error("Rsync apply failed", append(logFields, zap.Error(err))...)
		return fmt.Errorf("rsync command failed: %w. Output: %s", err, string(output))
	}

	if len(output) > 0 {
		log.Info("Rsync completed successfully", logFields...)
	} else {
		log.Info("Rsync completed successfully (no output)", zap.Duration("duration", duration))
	}

	return nil
}

// buildWebSocketURL constructs the WebSocket URL for the rsync sidecar.
func (rw *FileSyncer) buildWebSocketURL() string {
	u, err := url.Parse(rw.apiURL)
	if err != nil {
		log.Fatal("Invalid BIFROST_API_URL provided",
			zap.String("apiURL", rw.apiURL),
			zap.Error(err),
		)
	}

	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else {
		u.Scheme = "ws"
	}
	u.Path = fmt.Sprintf("/api/v1/push/sidecar/%s/%s", rw.appID, rw.deploymentID)

	return u.String()
}

// sendProtoMessage marshals and sends a protobuf message over the WebSocket.
func (rw *FileSyncer) sendProtoMessage(msg proto.Message) {
	data, err := proto.Marshal(msg)
	if err != nil {
		log.Error("Failed to marshal proto message",
			zap.String("messageType", fmt.Sprintf("%T", msg)),
			zap.Error(err),
		)
		return
	}
	if err := rw.conn.WriteMessage(websocket.BinaryMessage, data); err != nil {
		log.Warn("Failed to write proto message to websocket",
			zap.String("messageType", fmt.Sprintf("%T", msg)),
			zap.Int("sizeBytes", len(data)),
			zap.Error(err),
		)
	} else {
		log.Debug("Successfully sent proto message",
			zap.String("messageType", fmt.Sprintf("%T", msg)),
			zap.Int("sizeBytes", len(data)),
		)
	}
}

func buildPushResponse(pushID string, status pb.PushResponse_PushStatus, errorMessage string) *pb.WebsocketMessage {
	return &pb.WebsocketMessage{
		MessageType: pb.WebsocketMessage_PUSH_RESPONSE,
		Message: &pb.WebsocketMessage_PushResponse{
			PushResponse: &pb.PushResponse{
				Status:       status,
				ErrorMessage: errorMessage,
				PushId:       pushID,
			},
		},
	}
}
