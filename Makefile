SUPPORTED_PLATFORMS := linux/amd64,linux/arm64

SIDECAR_IMAGE := code-sync-sidecar
IMAGE_TAG := latest
DOCKER_IMAGE := conorbranagan/$(SIDECAR_IMAGE)
AWS_REGION := us-east-1

BINARIES_DIR := $(shell pwd)/code-sync-sidecar/binaries
RSYNC_VERSION := 3.4.1
BUILDX_BUILDER := multi-arch-builder

# Set the paths for protoc-gen-go
GOBIN ?= $(shell go env GOPATH)/bin
PATH := $(GOBIN):$(PATH)


help:
	@echo "Available commands:"
	@echo "  sidecar-push-ecr         - Build and push sidecar image to ECR"
	@echo "  sidecar-push-docker      - Build and push sidecar image to Docker"
	@echo ""
	@echo "  build-rsync-static-amd64 - Build static rsync binaries for amd64"
	@echo "  build-rsync-static-arm64 - Build static rsync binaries for arm64"	
	@echo "  build-rsync-static       - Build static rsync binaries for amd64 and arm64"

setup-buildx:
	@if ! docker buildx ls | grep -q "$(BUILDX_BUILDER)"; then \
		echo "Buildx builder $(BUILDX_BUILDER) not found. Creating..."; \
		docker buildx create --name $(BUILDX_BUILDER) --use --bootstrap; \
	else \
		echo "Using existing buildx builder $(BUILDX_BUILDER)."; \
		docker buildx use $(BUILDX_BUILDER); \
	fi

check-aws-profile:
	@if [ -z "$$AWS_PROFILE" ]; then \
		echo "Error: AWS_PROFILE environment variable is not set"; \
		exit 1; \
	fi

# Build Rsync static using Dockerfile
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
	# @if [ -f "$(OUTPUT_DIR)/rsync-$(RSYNC_VERSION)-linux-arm64" ]; then \
	#   echo "Rsync binaries already exist. Skipping build."; \
	# else \
	#   echo "Building static rsync binaries for arm64..." && \
	#   docker build -t rsync-static-builder-arm64 -f code-sync-sidecar/rsync/arm64.Dockerfile code-sync-sidecar/rsync; \
	#   docker run --rm -v $(OUTPUT_DIR):/output rsync-static-builder-arm64; \
	#   cp $(OUTPUT_DIR)/rsync-linux-arm64 $(OUTPUT_DIR)/rsync-$(RSYNC_VERSION)-linux-arm64; \
	#   echo "Build complete. Binaries are in ./$(OUTPUT_DIR)/"; \
	# fi

# Build Rsync static using Dockerfile
build-rsync-static: build-rsync-static-amd64 build-rsync-static-arm64
	@echo "All static Rsync binaries built successfully in $(BINARIES_DIR)"

# Build sidecar image
sidecar-push-ecr: check-aws-profile build-rsync-static setup-buildx
	$(eval ECR_REGISTRY := $(shell aws sts get-caller-identity --query Account --output text).dkr.ecr.$(AWS_REGION).amazonaws.com)
	$(eval IMG := $(ECR_REGISTRY)/$(SIDECAR_IMAGE):$(IMAGE_TAG)) # Using generic IMAGE_TAG, consider GIT_COMMIT too
	$(eval ECR_PASSWORD := $(shell aws ecr get-login-password --region $(AWS_REGION)))
	@echo $(ECR_PASSWORD) | docker login --username AWS --password-stdin $(ECR_REGISTRY) > /dev/null
	@aws ecr describe-repositories --repository-names $(SIDECAR_IMAGE) --region $(AWS_REGION) > /dev/null 2>&1 || \
		aws ecr create-repository --repository-name $(SIDECAR_IMAGE) --region $(AWS_REGION) > /dev/null
	docker buildx build --no-cache -f code-sync-sidecar/Dockerfile --platform $(SUPPORTED_PLATFORMS) -t $(IMG) --push .
	@echo "Sidecar image successfully pushed to $(IMG)"

sidecar-push-docker: build-rsync-static setup-buildx
	$(eval IMG := conorbranagan/$(SIDECAR_IMAGE):$(IMAGE_TAG)) # Using generic IMAGE_TAG
	docker buildx build --no-cache -f code-sync-sidecar/Dockerfile --platform $(SUPPORTED_PLATFORMS) -t $(IMG) --push .
	@echo "Sidecar image successfully pushed to $(IMG)"


#
# Proto commands
#

# FIXME: This depends on the bifrost core repo.
proto: install-protoc-gen-go
	# Check if bifrost repo exists
	@if [ ! -d "../bifrost" ]; then \
		echo "bifrost repo not found. Please clone the bifrost repo to the parent directory."; \
		exit 1; \
	fi

	mkdir -p pb && \
	protoc --proto_path=../bifrost/proto --go_out=pb --go_opt=paths=source_relative ../bifrost/proto/ws.proto

install-protoc-gen-go:
	@if ! command -v protoc-gen-go &> /dev/null; then \
		echo "protoc-gen-go not found. Installing..."; \
		go install google.golang.org/protobuf/cmd/protoc-gen-go@latest; \
	else \
		echo "protoc-gen-go already installed."; \
	fi
