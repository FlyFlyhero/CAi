"""Local CLI tool definitions for the CAi agent.

Local tools are YAML-based configs that tell the agent about externally
installed CLI programs (e.g. GROMACS, AMBER) that can be invoked via
#!BASH execution blocks.
"""

from .loader import LocalToolsLoader
from .section import LocalToolsSection
from .spec import LocalToolCommand, LocalToolSpec

__all__ = [
    "LocalToolCommand",
    "LocalToolsLoader",
    "LocalToolsSection",
    "LocalToolSpec",
]
