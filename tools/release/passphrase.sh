# passphrase.sh — sourced by release.sh and release-sign.sh: the release key's passphrase, with no
# question asked. They define ROOT and die.

# read_passphrase: `passphrase` from what FSBPYR_RELEASE_PASSPHRASE_FILE names, an absolute path. A
# program, an executable file that starts with "#!" or is an ELF file, or one the admin may run but
# not read, is run with nothing on its standard input, and prints it; any other file holds it, x
# bits or not (on exFAT or NTFS, or a vfat mounted by hand, every file has them). It is one line,
# and the LF after it does not count. What the program writes on its standard error is dropped:
# release.sh keeps what a step prints in its report.
#
# Whoever can write that file chooses what runs next to the release key, so it must be the admin's
# or root's and nobody else may write it. Nobody else may read a file that holds the passphrase. It
# is kept out of the repository, as the key is. The checks and the use are of one open file
# (/proc/self/fd), so a file put in its place after the checks, through a directory on the way
# that someone else may write, is never run or read. A program the admin may run but not read
# cannot be opened: it runs by its name, so the way to it must be one only the admin or root can
# change (safe_way).
read_passphrase() {
    local file=${FSBPYR_RELEASE_PASSPHRASE_FILE:-} way fd= owner mode status=0
    [ -n "$file" ] || die "no passphrase: set FSBPYR_RELEASE_PASSPHRASE_FILE to a file that" \
        "holds it, or to a program that prints it"
    case "$file" in
        /*) ;;
        *) die "FSBPYR_RELEASE_PASSPHRASE_FILE is not an absolute path: $file" ;;
    esac
    [ -f "$file" ] || die "FSBPYR_RELEASE_PASSPHRASE_FILE names no file: $file"
    way=$(realpath -- "$file")
    case "$way" in
        "$ROOT"/*) die "$file is inside this checkout: a passphrase is never kept in the repository" ;;
    esac
    if { exec {fd}<"$file"; } 2>/dev/null; then
        way=/proc/self/fd/$fd
        [ -f "$way" ] || die "FSBPYR_RELEASE_PASSPHRASE_FILE names no file: $file"
    else
        fd= && safe_way "$way" "$file"
    fi
    read -r owner mode <<<"$(stat -L -c '%u %a' -- "$way")"
    [ "$owner" = "$(id -u)" ] || [ "$owner" = 0 ] || die "$file belongs to someone else"
    (((8#$mode & 8#022) == 0)) || die "others can write $file"
    if [ -x "$way" ] && is_program "$way"; then
        passphrase=$("$way" </dev/null 2>/dev/null) || status=$?
        [ "$status" = 0 ] || die "the passphrase program $file failed (exit $status)"
    else
        (((8#$mode & 8#044) == 0)) || die "others can read $file"
        passphrase=$(cat <&"$fd")
    fi
    [ -z "$fd" ] || exec {fd}<&-
    [ -n "$passphrase" ] || die "$file gave no passphrase"
    [[ "$passphrase" != *$'\n'* ]] || die "$file gave more than one line"
}

# Dies unless every directory from / down to the one that holds `$1`, a path without links, is the
# admin's or root's and nobody else may write it: not others, nor a group with someone else in it
# (one of the admin's alone, as umask 002 leaves them writable, is the admin's), unless it is
# sticky (/tmp: nobody else may rename what is the admin's there). `$2` is the name given.
safe_way() {
    local dir=${1%/*} owner group mode
    while :; do
        dir=${dir:-/}
        read -r owner group mode <<<"$(stat -c '%u %g %a' -- "$dir")"
        [ "$owner" = "$(id -u)" ] || [ "$owner" = 0 ] || die "$dir, on the way to $2, belongs" \
            "to someone else"
        if (((8#$mode & 8#1000) == 0)); then
            (((8#$mode & 8#002) == 0)) || die "others can write $dir, on the way to $2"
            (((8#$mode & 8#020) == 0)) || admins_group "$group" || die "a group can write $dir," \
                "on the way to $2"
        fi
        [ "$dir" != / ] || return 0
        dir=${dir%/*}
    done
}

# Whether group `$1` is the admin's alone: the admin's own group, with nobody else listed in it.
admins_group() {
    local members
    [ "$1" = "$(id -g)" ] || return 1
    members=$(getent group "$1" | cut -d: -f4)
    [ -z "$members" ] || [ "$members" = "$(id -un)" ]
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
