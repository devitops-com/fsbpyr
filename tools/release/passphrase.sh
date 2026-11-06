# passphrase.sh — sourced by release.sh and release-sign.sh: the release key's passphrase, with no
# question asked. They define ROOT and die.

# read_passphrase: `passphrase` from what FSBPYR_RELEASE_PASSPHRASE_FILE names, an absolute path. A
# program, an executable file that starts with "#!" or is an ELF file, or one the admin may run but
# not read, is run with nothing on its standard input, and prints it; any other file holds it, x
# bits or not (on exFAT or NTFS, or a vfat mounted by hand, every file has them). It is one line, and the LF after it does not count. What
# the program writes on its standard error is dropped: release.sh keeps what a step prints in its
# report.
#
# Whoever can write that file chooses what runs next to the release key, so it must be the admin's
# or root's and nobody else may write it. Nobody else may read a file that holds the passphrase. It
# is kept out of the repository, as the key is.
read_passphrase() {
    local file=${FSBPYR_RELEASE_PASSPHRASE_FILE:-} owner mode status=0
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
    if [ -x "$file" ] && is_program "$file"; then
        passphrase=$("$file" </dev/null 2>/dev/null) || status=$?
        [ "$status" = 0 ] || die "the passphrase program $file failed (exit $status)"
    else
        (((8#$mode & 8#044) == 0)) || die "others can read $file"
        passphrase=$(<"$file")
    fi
    [ -n "$passphrase" ] || die "$file gave no passphrase"
    [[ "$passphrase" != *$'\n'* ]] || die "$file gave more than one line"
}

# Whether a file starts as a program does: "#!", or ELF's magic number. One that cannot be read
# (a helper of root's of mode 0711) counts: it cannot hold a passphrase.
is_program() {
    [ -r "$1" ] || return 0
    case "$(head -c 4 -- "$1" | od -An -tx1 | tr -d ' \n')" in
        2321*|7f454c46) return 0 ;;
    esac
    return 1
}
