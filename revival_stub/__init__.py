"""Minimal Chang'an Huanshihui 0.1.20.1 compatibility stub."""

from .codec import INITIAL_KEY, ReaderState, WriterState
from .registry import C2S_REGISTRY, S2C_REGISTRY, ProtocolRegistry, ProtocolSpec

__all__ = [
    "INITIAL_KEY",
    "ReaderState",
    "WriterState",
    "ProtocolRegistry",
    "ProtocolSpec",
    "C2S_REGISTRY",
    "S2C_REGISTRY",
]

