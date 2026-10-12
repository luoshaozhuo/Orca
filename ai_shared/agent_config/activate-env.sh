#!/usr/bin/env bash
# Codex 通过 BASH_ENV source；Claude SessionStart 执行并登记到 CLAUDE_ENV_FILE。
_orca_activate() {
    local conda_init="$HOME/miniconda3/etc/profile.d/conda.sh"
    if [[ ! -f "$conda_init" ]]; then
        printf 'orca: Conda initialization script missing: %s\n' "$conda_init" >&2
        return 1
    fi
    source "$conda_init" && conda activate orca
}

if ! _orca_activate; then
    printf 'orca: failed to activate Conda environment\n' >&2
    exit 1
fi
unset -f _orca_activate

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [[ -z "${CLAUDE_ENV_FILE:-}" ]]; then
        printf 'orca: SessionStart requires CLAUDE_ENV_FILE\n' >&2
        exit 1
    fi
    printf 'source %q\n' "$(realpath "${BASH_SOURCE[0]}")" >> "$CLAUDE_ENV_FILE"
fi
