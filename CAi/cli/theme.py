"""CLI theme configuration and console setup."""

from rich.console import Console
from rich.theme import Theme

# Deep space aesthetic with neon accents
THEME = Theme({
    "cai.primary": "bold #61afef",       # Electric Blue
    "cai.secondary": "#c678dd",          # Soft Purple
    "cai.accent": "#98c379",             # Green (success/action)
    "cai.warn": "#e5c07b",              # Warm Yellow
    "cai.dim": "#5c6370",               # Muted gray
    "cai.text": "#abb2bf",              # Light text
    "cai.border": "#3e4452",            # Subtle border
    "cai.highlight": "#e06c75",         # Red/Pink highlight
    "cai.cyan": "bold #56b6c2",         # Cyan for special elements
})

console = Console(theme=THEME)
