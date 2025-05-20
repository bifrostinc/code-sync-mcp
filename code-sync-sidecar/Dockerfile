# Start with a slim base image
FROM golang:1.24-alpine AS builder

WORKDIR /app
COPY code-sync-sidecar/ .

# Build the Go application statically
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o code-sync-sidecar .

# Final minimal image
FROM alpine:3.18

WORKDIR /app

# Copy the binary from the builder stage
COPY --from=builder /app/code-sync-sidecar /app/code-sync-sidecar
RUN mkdir -p /app/bin


COPY code-sync-sidecar/binaries/rsync-*-linux-amd64 /app/bin/rsync_amd64
COPY code-sync-sidecar/binaries/rsync-*-linux-arm64 /app/bin/rsync_arm64

RUN echo '#!/bin/sh' > /app/bin/rsync && \
    echo 'arch=$(uname -m)' >> /app/bin/rsync && \
    echo 'if [ "$arch" = "x86_64" ]; then' >> /app/bin/rsync && \
    echo '  exec /app/bin/rsync_amd64 "$@"' >> /app/bin/rsync && \
    echo 'elif [ "$arch" = "aarch64" ]; then' >> /app/bin/rsync && \
    echo '  exec /app/bin/rsync_arm64 "$@"' >> /app/bin/rsync && \
    echo 'else' >> /app/bin/rsync && \
    echo '  echo "Unsupported architecture: $arch"' >> /app/bin/rsync && \
    echo '  exit 1' >> /app/bin/rsync && \
    echo 'fi' >> /app/bin/rsync && \
    chmod +x /app/bin/rsync

COPY code-sync-sidecar/launcher-script/rsync-launcher.sh /app/bin/rsync-launcher.sh
RUN chmod +x /app/bin/rsync-launcher.sh


# Add the bin directory to PATH
ENV PATH="/app/bin:${PATH}"

# Run the file watcher
CMD ["/app/code-sync-sidecar"]