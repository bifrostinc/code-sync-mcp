package log

import (
	"log"

	"go.uber.org/zap"
)

var (
	// Log is the global logger instance. It's initialized with a no-op logger
	// until Init is called, preventing nil pointer panics.
	Log *zap.Logger = zap.NewNop()
)

// Init initializes the global logger with the specified configuration.
// It should be called once at the beginning of the application.
func Init(serviceName string, initialFields map[string]string) {
	// Configure the logger (e.g., Production config)
	// You might want to make the level configurable (e.g., via env var or flag)
	config := zap.NewProductionConfig()
	// Example: Set level based on an environment variable
	// levelStr := os.Getenv("LOG_LEVEL")
	// if levelStr != "" {
	// 	var level zapcore.Level
	// 	if err := level.Set(levelStr); err == nil {
	// 		config.Level.SetLevel(level)
	// 	} else {
	// 		// Use default log package for this initial error as zap isn't ready
	// 		log.Printf("Warning: Invalid LOG_LEVEL '%s', using default: %v", levelStr, config.Level.Level())
	// 	}
	// }

	// Build the base logger
	baseLogger, err := config.Build(zap.AddCallerSkip(1)) // Add caller skip so log lines show caller of Info/Warn/etc.
	if err != nil {
		// Fallback to standard log if zap fails initialization
		log.Fatalf("Failed to initialize zap logger: %v", err)
	}

	// Add service name and any other initial fields
	fields := []zap.Field{zap.String("service", serviceName)}
	for k, v := range initialFields {
		fields = append(fields, zap.String(k, v))
	}
	Log = baseLogger.With(fields...)

	// Redirect standard log output to zap
	// This ensures libraries using the standard log package also have their output captured.
	// Note: This returns a function to undo the redirection, which we call on defer Log.Sync()
	// to ensure cleanup happens correctly.
	undoRedirect := zap.RedirectStdLog(Log)

	// It's good practice to sync the logger on shutdown.
	// Since this Init function runs once, we can't directly defer the Sync here.
	// The caller (main.go) should ensure Log.Sync() is deferred.
	// However, we *can* defer the undo function for the std log redirection.
	// This might be slightly complex if the program exits abruptly, but is generally okay.
	// A more robust solution involves signal handling in main to explicitly sync and undo.
	// For simplicity here, we rely on main deferring Sync.
	_ = undoRedirect // Keep the variable alive for potential future use or explicit undo call

	Log.Info("Global logger initialized")
}

// Sync flushes any buffered log entries. Applications should take care to call
// Sync before exiting. This is often done using `defer log.Sync()`.
func Sync() {
	if Log != nil {
		_ = Log.Sync() // Ignore Sync errors for simplicity
	}
}

// --- Helper functions ---

// Debug logs a message at DebugLevel. The message includes any fields passed
// at the log site, as well as any fields accumulated on the logger.
func Debug(msg string, fields ...zap.Field) {
	Log.Debug(msg, fields...)
}

// Info logs a message at InfoLevel. The message includes any fields passed
// at the log site, as well as any fields accumulated on the logger.
func Info(msg string, fields ...zap.Field) {
	Log.Info(msg, fields...)
}

// Warn logs a message at WarnLevel. The message includes any fields passed
// at the log site, as well as any fields accumulated on the logger.
func Warn(msg string, fields ...zap.Field) {
	Log.Warn(msg, fields...)
}

// Error logs a message at ErrorLevel. The message includes any fields passed
// at the log site, as well as any fields accumulated on the logger.
func Error(msg string, fields ...zap.Field) {
	Log.Error(msg, fields...)
}

// Fatal logs a message at FatalLevel, then calls os.Exit(1).
func Fatal(msg string, fields ...zap.Field) {
	Log.Fatal(msg, fields...)
}

// With creates a child logger and adds structured context to it. Fields added
// to the child don't affect the parent, and vice versa.
func With(fields ...zap.Field) *zap.Logger {
	return Log.With(fields...)
}
