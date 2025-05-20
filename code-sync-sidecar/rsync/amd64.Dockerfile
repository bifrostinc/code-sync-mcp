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
COPY build-amd64.sh /build-amd64.sh

# Make the script executable
RUN chmod +x /build-amd64.sh

# Set the working directory
WORKDIR /

# Run the build script
CMD ["/build-amd64.sh"]