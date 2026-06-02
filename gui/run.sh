#!/bin/bash
# Launch the TVNS BIDS Pipeline GUI.
# Run from anywhere: bash tvns/gui/run.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer the Neuroimaging conda env if available (has tkinter)
for base in "$HOME/anaconda3" "$HOME/miniconda3" /opt/anaconda3 /opt/miniconda3; do
    candidate="$base/envs/Neuroimaging/bin/python"
    if [ -x "$candidate" ]; then
        exec "$candidate" "$SCRIPT_DIR/app.py" "$@"
    fi
done

# Fall back to system python3
exec python3 "$SCRIPT_DIR/app.py" "$@"
