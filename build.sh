#!/usr/bin/env bash
set -euo pipefail

echo "Building CSS..."
npm ci
npm run build:css

echo "Preparing dist/..."
rm -rf dist
mkdir -p dist

# Copy HTML pages
cp -v ./*.html dist/

# Copy assets (avoid dist/js/js and dist/images/images)
mkdir -p dist/css dist/js dist/images

cp -v css/output.css dist/css/

# Copy directory CONTENTS (not the directory itself)
cp -R js/. dist/js/
cp -R images/. dist/images/

# Root files
[ -f robots.txt ] && cp -v robots.txt dist/ || true
[ -f sitemap.xml ] && cp -v sitemap.xml dist/ || true

# Sanity checks
test -f dist/index.html || { echo "ERROR: dist/index.html missing"; exit 1; }
test -f dist/css/output.css || { echo "ERROR: dist/css/output.css missing"; exit 1; }

echo "Build complete. Output in dist/"
