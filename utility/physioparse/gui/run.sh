#!/bin/bash
# Launch the Pseudotime Pipeline GUI.
#
# Usage: bash run.sh [conda_env_name]
#   Optional: pass the name of a conda environment to use its Python.
#   Example: bash run.sh Neuroimaging
#   If omitted, searches for any available Python with tkinter.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${1:-}"

# ── 1. Try the requested (or any) conda env by direct path ────────────────────
CONDA_PYTHON=""

search_conda_env() {
    local env="$1"
    for base in "$HOME/anaconda3" "$HOME/miniconda3" "/opt/anaconda3" "/opt/miniconda3"; do
        candidate="$base/envs/$env/bin/python"
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ -n "$ENV_NAME" ]; then
    CONDA_PYTHON="$(search_conda_env "$ENV_NAME")"
    if [ -z "$CONDA_PYTHON" ]; then
        echo "WARNING: conda env '$ENV_NAME' not found by direct path."
    fi
fi

# ── 2. Try conda activate as a fallback ───────────────────────────────────────
if [ -z "$CONDA_PYTHON" ] && [ -n "$ENV_NAME" ]; then
    CONDA_BASE="$(conda info --base 2>/dev/null)"
    if [ -n "$CONDA_BASE" ]; then
        # shellcheck disable=SC1091
        source "$CONDA_BASE/etc/profile.d/conda.sh"
        conda activate "$ENV_NAME" 2>/dev/null && CONDA_PYTHON="$(which python)"
    fi
fi

# ── 3. Last resort: system python3 ────────────────────────────────────────────
if [ -z "$CONDA_PYTHON" ]; then
    echo "No conda env specified or found — using system python3."
    echo "TIP: run as:  bash run.sh <your_conda_env_name>"
    CONDA_PYTHON="$(which python3)"
fi

echo "Python: $CONDA_PYTHON"
echo "Starting Pseudotime Pipeline GUI…"
"$CONDA_PYTHON" "$DIR/app.py"
