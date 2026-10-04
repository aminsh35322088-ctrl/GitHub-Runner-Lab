#!/usr/bin/env bash
set -Eeuo pipefail

ALIAS_USER="${LAB_SSH_ALIAS_USER:-root}"
TARGET_USER="${LAB_SSH_TARGET_USER:-runner}"
WRAPPER_PATH="${LAB_SSH_ALIAS_WRAPPER:-/usr/local/sbin/github-lab-root-as-runner}"

die() {
  echo "[ssh-user-alias] $*" >&2
  exit 1
}

[[ "$ALIAS_USER" == "root" ]] || die "only the root compatibility alias is supported"

alias_entry="$(getent passwd "$ALIAS_USER" || true)"
target_entry="$(getent passwd "$TARGET_USER" || true)"
[[ -n "$alias_entry" ]] || die "alias user '$ALIAS_USER' does not exist"
[[ -n "$target_entry" ]] || die "target user '$TARGET_USER' does not exist"

alias_uid="$(cut -d: -f3 <<<"$alias_entry")"
target_uid="$(cut -d: -f3 <<<"$target_entry")"
target_home="$(cut -d: -f6 <<<"$target_entry")"
target_shell="$(cut -d: -f7 <<<"$target_entry")"
runuser_bin="$(command -v runuser || true)"

[[ "$alias_uid" == "0" ]] || die "'$ALIAS_USER' is not UID 0"
[[ "$target_uid" != "0" ]] || die "target user must be non-root"
[[ -d "$target_home" ]] || die "target home '$target_home' does not exist"
[[ -x "$target_shell" ]] || die "target shell '$target_shell' is not executable"
[[ -n "$runuser_bin" && -x "$runuser_bin" ]] || die "runuser is unavailable"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

cat >"$tmp" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_USER=$(printf '%q' "$TARGET_USER")
TARGET_HOME=$(printf '%q' "$target_home")
TARGET_SHELL=$(printf '%q' "$target_shell")
RUNUSER=$(printf '%q' "$runuser_bin")

cd "\$TARGET_HOME"
exec "\$RUNUSER" -u "\$TARGET_USER" -- /usr/bin/env \
  HOME="\$TARGET_HOME" \
  USER="\$TARGET_USER" \
  LOGNAME="\$TARGET_USER" \
  SHELL="\$TARGET_SHELL" \
  "\$TARGET_SHELL" "\$@"
EOF

sudo install -o root -g root -m 0755 "$tmp" "$WRAPPER_PATH"

if ! grep -Fxq "$WRAPPER_PATH" /etc/shells; then
  printf '%s\n' "$WRAPPER_PATH" | sudo tee -a /etc/shells >/dev/null
fi

current_shell="$(getent passwd "$ALIAS_USER" | cut -d: -f7)"
if [[ "$current_shell" != "$WRAPPER_PATH" ]]; then
  sudo usermod --shell "$WRAPPER_PATH" "$ALIAS_USER"
fi

installed_shell="$(getent passwd "$ALIAS_USER" | cut -d: -f7)"
[[ "$installed_shell" == "$WRAPPER_PATH" ]] || die "failed to install root compatibility shell"

actual_user="$(sudo "$WRAPPER_PATH" -c 'id -un')"
actual_uid="$(sudo "$WRAPPER_PATH" -c 'id -u')"
actual_home="$(sudo "$WRAPPER_PATH" -c 'printf "%s" "$HOME"')"

[[ "$actual_user" == "$TARGET_USER" ]] || die "compatibility shell resolved to '$actual_user'"
[[ "$actual_uid" == "$target_uid" ]] || die "compatibility shell resolved to UID '$actual_uid'"
[[ "$actual_home" == "$target_home" ]] || die "compatibility shell HOME is '$actual_home'"

echo "[ssh-user-alias] root SSH requests will execute as $TARGET_USER (uid=$target_uid)"
