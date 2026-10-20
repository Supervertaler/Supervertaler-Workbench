#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# Build the Okapi Sidecar fat JAR
#
# Prerequisites: Java 17+ and Maven 3.8+
#
# Usage:
#   ./build.sh              Build the JAR
#   ./build.sh --run        Build and run locally
#   ./build.sh --docker     Build Docker image
#   ./build.sh --jlink      Build JAR + minimal JRE bundle
# ────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

JAR_NAME="okapi-sidecar"
PORT="${PORT:-8090}"

build_jar() {
    echo "🔨 Building $JAR_NAME..."
    mvn package -DskipTests -B
    echo "✅ Built: target/$JAR_NAME-0.1.9.jar"
}

run_local() {
    build_jar
    echo "🚀 Starting sidecar on port $PORT..."
    java -jar "target/$JAR_NAME-0.1.9.jar" --port="$PORT"
}

build_docker() {
    echo "🐳 Building Docker image..."
    docker build -t supervertaler-okapi-sidecar:latest .
    echo "✅ Docker image built: supervertaler-okapi-sidecar:latest"
    echo ""
    echo "Run with:"
    echo "  docker run -p 8090:8090 supervertaler-okapi-sidecar:latest"
}

build_jlink() {
    build_jar
    echo ""
    echo "📦 Creating minimal JRE bundle with jlink..."

    # Detect JAVA_HOME
    if [ -z "${JAVA_HOME:-}" ]; then
        echo "❌ JAVA_HOME not set. Needed for jlink."
        exit 1
    fi

    JLINK="$JAVA_HOME/bin/jlink"
    if [ ! -f "$JLINK" ]; then
        echo "❌ jlink not found at $JLINK"
        echo "   Make sure you have a full JDK (not just JRE) installed."
        exit 1
    fi

    # Determine required modules (conservative set for Okapi)
    MODULES="java.base,java.xml,java.logging,java.desktop,java.naming,java.sql,java.management,jdk.crypto.ec"

    OUTPUT_DIR="target/jre-bundle"
    rm -rf "$OUTPUT_DIR"

    "$JLINK" \
        --add-modules "$MODULES" \
        --strip-debug \
        --no-man-pages \
        --no-header-files \
        --compress=2 \
        --output "$OUTPUT_DIR"

    echo "✅ Minimal JRE created: $OUTPUT_DIR"
    echo "   Size: $(du -sh "$OUTPUT_DIR" | cut -f1)"

    # Create dist/ folder
    echo "📦 Creating dist/ bundle..."
    rm -rf dist
    mkdir -p dist/jre
    cp "target/$JAR_NAME-0.1.9.jar" dist/okapi-sidecar.jar
    cp -r "$OUTPUT_DIR/"* dist/jre/
    echo "✅ Distribution ready: dist/"
    echo "   JAR: dist/okapi-sidecar.jar"
    echo "   JRE: dist/jre/"
    echo "   Total: $(du -sh dist | cut -f1)"
}

# ── Main ─────────────────────────────────────────────────
case "${1:-}" in
    --run)    run_local ;;
    --docker) build_docker ;;
    --jlink)  build_jlink ;;
    --dist)   build_jlink ;;  # alias
    *)        build_jar ;;
esac
