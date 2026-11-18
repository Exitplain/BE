"""옮김: agent/source/detect_framework.py (백엔드 코드 연결이 같은 분석기를 쓴다). 기존 검증 스크립트 호환용."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from agent.source.detect_framework import *  # noqa: E402,F401,F403
from agent.source.detect_framework import detect, iter_files, next_layout, read_deps  # noqa: E402,F401
