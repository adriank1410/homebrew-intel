#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.coverage_sync import main

raise SystemExit(main())
