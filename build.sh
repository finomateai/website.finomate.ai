#!/usr/bin/env bash
set -euo pipefail

echo "Building CSS..."
npm run build:css

echo "Preparing dist/..."
rm -rf dist
mkdir -p dist/css dist/js dist/images

cp *.html dist/
cp css/output.css dist/css/
cp -r js/ dist/js/
cp -r images/ dist/images/
cp robots.txt dist/
cp sitemap.xml dist/

echo "Build complete. Output in dist/"
