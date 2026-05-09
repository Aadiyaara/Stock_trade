#!/bin/bash
# Run this after cloning to enable the pre-commit hook
git config core.hooksPath .githooks
echo "Git hooks configured. KNOWLEDGE.md updates are now enforced on commit."
