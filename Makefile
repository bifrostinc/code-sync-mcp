SUPPORTED_PLATFORMS := linux/amd64,linux/arm64
DOCKER_IMAGE := conorbranagan/code-sync-sidecar
IMAGE_TAG := latest

BINARIES_DIR := $(shell pwd)/code-sync-sidecar/binaries
RSYNC_VERSION := 3.4.1
BUILDX_BUILDER := multi-arch-builder

help:
	@echo "Available commands:"
	@echo "  sidecar-docker           - Build and push sidecar image to Docker"
	@echo "  build-rsync-static       - Build static rsync binaries for amd64 and arm64"

setup-buildx:
	@if ! docker buildx ls | grep -q "$(BUILDX_BUILDER)"; then \
		echo "Buildx builder $(BUILDX_BUILDER) not found. Creating..."; \
		docker buildx create --name $(BUILDX_BUILDER) --use --bootstrap; \
	else \
		echo "Using existing buildx builder $(BUILDX_BUILDER)."; \
		docker buildx use $(BUILDX_BUILDER); \
	fi

build-rsync-static-amd64:
	mkdir -p $(BINARIES_DIR)
	@echo "Checking if rsync binaries already exist..."
	@if [ -f "$(BINARIES_DIR)/rsync-$(RSYNC_VERSION)-linux-amd64" ]; then \
		echo "Rsync binaries already exist. Skipping build."; \
	else \
		echo "Building static rsync binaries for amd64..." && \
		docker build -t rsync-static-builder-amd64 -f code-sync-sidecar/rsync/amd64.Dockerfile code-sync-sidecar/rsync; \
		docker run --rm -v $(BINARIES_DIR):/output rsync-static-builder-amd64; \
		cp $(BINARIES_DIR)/rsync-linux-amd64 $(BINARIES_DIR)/rsync-$(RSYNC_VERSION)-linux-amd64; \
		echo "Build complete. Binaries are in ./$(BINARIES_DIR)/"; \
	fi

build-rsync-static-arm64:
	mkdir -p $(BINARIES_DIR)
	@echo "Checking if rsync binaries already exist..."
	@echo "ARM64 rsync build is currently disabled in Makefile"
	@if [ -f "$(OUTPUT_DIR)/rsync-$(RSYNC_VERSION)-linux-arm64" ]; then \
	  echo "Rsync binaries already exist. Skipping build."; \
	else \
	  echo "Building static rsync binaries for arm64..." && \
	  docker build -t rsync-static-builder-arm64 -f code-sync-sidecar/rsync/arm64.Dockerfile code-sync-sidecar/rsync; \
	  docker run --rm -v $(OUTPUT_DIR):/output rsync-static-builder-arm64; \
	  cp $(OUTPUT_DIR)/rsync-linux-arm64 $(OUTPUT_DIR)/rsync-$(RSYNC_VERSION)-linux-arm64; \
	  echo "Build complete. Binaries are in ./$(OUTPUT_DIR)/"; \
	fi

build-rsync-static: build-rsync-static-amd64 build-rsync-static-arm64
	@echo "All static Rsync binaries built successfully in $(BINARIES_DIR)"

sidecar-docker: build-rsync-static setup-buildx
	$(eval IMG := $(DOCKER_IMAGE):$(IMAGE_TAG)) # Using generic IMAGE_TAG
	docker buildx build --no-cache -f code-sync-sidecar/Dockerfile --platform $(SUPPORTED_PLATFORMS) -t $(IMG) --push .
	@echo "Sidecar image successfully pushed to $(IMG)"
