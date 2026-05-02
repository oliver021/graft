#!/bin/bash
# record_demo.sh — Record a Graft showcase GIF
#
# Prerequisites:
#   pip install asciinema agg
#   graft installed (pip install -e .)
#
# Usage:
#   bash examples/record_demo.sh
#
# This will guide you through recording an interactive demo, then convert to GIF.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="/tmp/graft-demo-$$"
OUTPUT_GIF="$REPO_ROOT/docs/demo.gif"

echo "==================================================================="
echo "Graft Demo Recorder"
echo "==================================================================="
echo ""
echo "This script will:"
echo "  1. Create a temp directory for the demo"
echo "  2. Start asciinema recording"
echo "  3. Guide you through 8 showcase queries"
echo "  4. Convert to GIF"
echo ""
echo "Prerequisites:"
echo "  pip install -e '.[demo]'  (installs asciinema and agg)"
echo ""

# Check for required tools
if ! command -v asciinema &> /dev/null; then
    echo "ERROR: asciinema not found."
    echo "Install with: pip install -e '.[demo]'"
    exit 1
fi
if ! command -v agg &> /dev/null; then
    echo "ERROR: agg not found."
    echo "Install with: pip install -e '.[demo]'"
    exit 1
fi
if ! command -v graft &> /dev/null; then
    echo "ERROR: graft not found. Install with: cd $REPO_ROOT && pip install -e ."
    exit 1
fi

# Create demo directory
mkdir -p "$DEMO_DIR"
cd "$DEMO_DIR"

echo "Demo directory: $DEMO_DIR"
echo "Output GIF will be: $OUTPUT_GIF"
echo ""
echo "Starting asciinema recording..."
echo "You will type commands. Pause ~1-2 seconds between each (or let them run)."
echo "Press Ctrl+D when done."
echo ""
read -p "Press ENTER to start recording..."

# Start recording
asciinema rec demo.cast --idle-time-limit 1.0

echo ""
echo "==================================================================="
echo "Recording saved. Now converting to GIF..."
echo "==================================================================="
echo ""

# Create docs directory
mkdir -p "$REPO_ROOT/docs"

# Convert to GIF
echo "Converting demo.cast to GIF (this takes ~30 seconds)..."
agg demo.cast "$OUTPUT_GIF"

echo ""
echo "✓ GIF created: $OUTPUT_GIF"
ls -lh "$OUTPUT_GIF"

echo ""
echo "==================================================================="
echo "Next steps:"
echo "==================================================================="
echo ""
echo "1. Review the GIF in your file manager"
echo "2. Uncomment the GIF line in README.md:"
echo "   Open: $REPO_ROOT/README.md"
echo "   Find the comment: <!-- ![Graft demo](docs/demo.gif) -->"
echo "   Change to:       ![Graft demo](docs/demo.gif)"
echo ""
echo "3. Commit and push:"
echo "   cd $REPO_ROOT"
echo "   git add docs/demo.gif README.md"
echo "   git commit -m 'add demo GIF'"
echo ""
echo "Cleanup (optional):"
echo "   rm -rf $DEMO_DIR"
echo ""
