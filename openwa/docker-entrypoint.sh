#!/bin/sh
set -eu

cleanup_profile() {
    profile_dir=$1
    [ -d "${profile_dir}" ] || return 0

    for artifact in SingletonLock SingletonSocket SingletonCookie DevToolsActivePort; do
        artifact_path=${profile_dir}/${artifact}
        if [ -e "${artifact_path}" ] || [ -L "${artifact_path}" ]; then
            rm -f -- "${artifact_path}"
        fi
    done
}

cleanup_profile "${OPENWA_PROFILE_DIR:-/sessions/appointment-notifier}"

for profile_dir in /sessions/profiles/*; do
    cleanup_profile "${profile_dir}"
done

exec "$@"
