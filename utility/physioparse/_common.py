#!/usr/bin/env python3
"""Shared helpers used by multiple physioparse scripts."""

import json
import os
import re

_TASK_PATTERNS = [
    (re.compile(r'REST_ep2d',      re.IGNORECASE), 'rest'),
    (re.compile(r'ContinuousStim', re.IGNORECASE), 'ContinuousStim'),
    (re.compile(r'BlockStim',      re.IGNORECASE), 'BlockStim'),
    (re.compile(r'TOPUP_AP',       re.IGNORECASE), 'AP'),
    (re.compile(r'TOPUP_PA',       re.IGNORECASE), 'PA'),
    (re.compile(r'FreeBreathe',    re.IGNORECASE), 'FreeBreath'),
    (re.compile(r'PaceBreathe',    re.IGNORECASE), 'PaceBreath'),
    (re.compile(r'BEAT_1p6',       re.IGNORECASE), 'BEAT'),
]


def _series_desc_to_task(series_desc):
    for pattern, task in _TASK_PATTERNS:
        if pattern.search(series_desc):
            return task
    return None


def _tr_from_json(fname, data_dir):
    """Read RepetitionTime from the BIDS JSON sidecar for this sequence."""
    if not data_dir:
        return None
    path = os.path.join(data_dir, fname)
    try:
        with open(path) as f:
            return json.load(f).get('RepetitionTime')
    except Exception:
        return None
