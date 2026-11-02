# passphrase.sh — sourced by release.sh and release-sign.sh: the release key's passphrase, with no
# question asked. They define ROOT and die.

# read_passphrase: `passphrase` from what FSBPYR_RELEASE_PASSPHRASE_FILE names, an absolute path. An
# executable file is run, with nothing on its standard input, and prints it; any other file holds
# it. It is one line, and the LF after it does not count.
#
# Whoever can write that file chooses what runs next to the release key, so it must be the admin's
# or root's and nobody else may write it. Nobody else may read a file that holds the passphrase. It
# is kept out of the repository, as the key is.
read_passphrase() {
    local file=${FSBPYR_RELEASE_PASSPHRASE_FILE:-} owner mode
    [ -n "$file" ] || die "no passphrase: set FSBPYR_RELEASE_PASSPHRASE_FILE to a file that" \
        "holds it, or to a program that prints it"
    case "$file" in
        /*) ;;
        *) die "FSBPYR_RELEASE_PASSPHRASE_FILE is not an absolute path: $file" ;;
    esac
    [ -f "$file" ] || die "FSBPYR_RELEASE_PASSPHRASE_FILE names no file: $file"
    case "$(realpath -- "$file")" in
        "$ROOT"/*) die "$file is inside this checkout: a passphrase is never kept in the repository" ;;
    esac
    read -r owner mode <<<"$(stat -L -c '%u %a' -- "$file")"
    [ "$owner" = "$(id -u)" ] || [ "$owner" = 0 ] || die "$file belongs to someone else"
    (((8#$mode & 8#022) == 0)) || die "others can write $file"
    if [ -x "$file" ]; then
        passphrase=$("$file" </dev/null) || die "$file failed"
    else
        (((8#$mode & 8#044) == 0)) || die "others can read $file"
        passphrase=$(<"$file")
    fi
    [ -n "$passphrase" ] || die "$file gave no passphrase"
    [[ "$passphrase" != *$'\n'* ]] || die "$file gave more than one line"
}
