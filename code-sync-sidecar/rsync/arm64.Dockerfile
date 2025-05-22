FROM ubuntu:22.04

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
    wget \
    git \
    build-essential \
    xz-utils \
    file \
    automake \
    autoconf \
    libtool \
    pkg-config \
    coreutils \
    python3 \
    python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Copy the build script into the container
COPY build-arm64.sh /build-arm64.sh

# Make the script executable
RUN chmod +x /build-arm64.sh

# Set the working directory
WORKDIR /

# Run the build script
CMD ["/build-arm64.sh"]