SUPPORTED_PLATFORMS := linux/amd64,linux/arm64
DOCKER_IMAGE := bifrostinc/code-sync-sidecar
IMAGE_TAG := latest

BINARIES_DIR := $(shell pwd)/code-sync-sidecar/binaries
RSYNC_VERSION := 3.4.1
BUILDX_BUILDER := multi-arch-builder

# PyPI settings
PACKAGE_NAME := code-sync-mcp
DIST_DIR := dist

help:
	@echo "Available commands:"
	@echo "  sidecar-docker         - Build and push sidecar image to Docker"
	@echo "  sidecar-local          - Build sidecar image locally for docker-compose"
	@echo "  build-rsync-static     - Build static rsync binaries for amd64 and arm64"
	@echo "  mcp-pypi-build         - Build Python package for PyPI"
	@echo "  mcp-pypi-test          - Upload package to TestPyPI"
	@echo "  mcp-pypi-publish       - Upload package to production PyPI"
	@echo "  mcp-pypi-clean         - Clean build artifacts"
	@echo "  mcp-pypi-install-test  - Install package from TestPyPI"
	@echo "  proto                  - Generate protobuf files"

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

sidecar-local: build-rsync-static-amd64
	@echo "Building sidecar image for local use (linux/amd64 platform)..."
	docker build --no-cache --platform linux/amd64 -f code-sync-sidecar/Dockerfile -t $(DOCKER_IMAGE):$(IMAGE_TAG) .
	@echo "Sidecar image built locally as $(DOCKER_IMAGE):$(IMAGE_TAG)"

# MCP Server PyPI related commands
mcp-pypi-clean:
	@echo "Cleaning build artifacts..."
	rm -rf code-sync-mcp/$(DIST_DIR)/ code-sync-mcp/build/ code-sync-mcp/*.egg-info/
	@echo "Clean complete."

mcp-pypi-build: mcp-pypi-clean
	@echo "Building Python package..."
	uv --directory code-sync-mcp run python -m build
	@echo "Build complete. Artifacts in code-sync-mcp/$(DIST_DIR)/"

mcp-pypi-test: mcp-pypi-build
	@echo "Uploading to TestPyPI..."
	@echo "Use '__token__' as username and your TestPyPI API token as password"
	uv --directory code-sync-mcp run twine upload --repository testpypi code-sync-mcp/$(DIST_DIR)/*
	@echo "Upload to TestPyPI complete."
	@echo "Install with: pip install --index-url https://test.pypi.org/simple/ $(PACKAGE_NAME)"

mcp-pypi-publish: mcp-pypi-build
	@echo "Uploading to production PyPI..."
	@echo "Use '__token__' as username and your PyPI API token as password"
	uv --directory code-sync-mcp run twine upload $(DIST_DIR)/*
	@echo "Upload to PyPI complete."
	@echo "Install with: pip install $(PACKAGE_NAME)"

mcp-pypi-install-test:
	@echo "Installing $(PACKAGE_NAME) from TestPyPI..."
	uv pip install --index-url https://test.pypi.org/simple/ $(PACKAGE_NAME)

# Convenience target for full PyPI workflow
mcp-pypi-workflow: mcp-pypi-test
	@echo ""
	@echo "Package uploaded to TestPyPI. Test it with:"
	@echo "  make mcp-pypi-install-test"
	@echo ""
	@echo "If everything works, publish to production PyPI with:"
	@echo "  make mcp-pypi-publish"


proto: install-protoc-gen-go
	mkdir -p code-sync-sidecar/pb code-sync-proxy/src/code_sync_proxy/pb code-sync-mcp/src/code_sync_mcp/pb && \
	protoc --proto_path=proto --go_out=code-sync-sidecar/pb --go_opt=paths=source_relative proto/ws.proto && \
	protoc --proto_path=proto --python_out=code-sync-proxy/src/code_sync_proxy/pb proto/ws.proto && \
	protoc --proto_path=proto --python_out=code-sync-mcp/src/code_sync_mcp/pb proto/ws.proto

# Set the paths for protoc-gen-go
GOBIN ?= $(shell go env GOPATH)/bin
PATH := $(GOBIN):$(PATH)

install-protoc-gen-go:
	@if ! command -v protoc-gen-go &> /dev/null; then \
		echo "protoc-gen-go not found. Installing..."; \
		go install google.golang.org/protobuf/cmd/protoc-gen-go@latest; \
	else \
		echo "protoc-gen-go already installed."; \
	fi
