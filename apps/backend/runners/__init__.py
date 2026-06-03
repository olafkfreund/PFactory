"""Runners package.

Historically this re-exported a set of standalone AIFactory CLI runners
(ideation / spec / roadmap / insights / ai_analyzer). Those are not part of
PFactory and several imported modules that no longer exist, so the eager
re-exports here crashed any ``import runners.*`` — including the
``runners.github.providers`` imports the web-server still uses. Removed in
#43; submodules are imported directly by their consumers.
"""
