/* Minimal file-syscall tracer (strace is not installed on the dev host).
 *
 * usage: ftrace CMD [ARGS...]   -> prints "syscall(path) = ret" for path-taking syscalls
 * x86_64 Linux only; follows the direct child only (no fork following).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/syscall.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <unistd.h>

struct sysdesc { long nr; const char *name; int path_arg; };

static const struct sysdesc TRACED[] = {
    {SYS_open, "open", 0},           {SYS_stat, "stat", 0},
    {SYS_lstat, "lstat", 0},         {SYS_access, "access", 0},
    {SYS_readlink, "readlink", 0},   {SYS_execve, "execve", 0},
    {SYS_openat, "openat", 1},       {SYS_newfstatat, "newfstatat", 1},
    {SYS_readlinkat, "readlinkat", 1}, {SYS_faccessat, "faccessat", 1},
    {SYS_faccessat2, "faccessat2", 1}, {SYS_statx, "statx", 1},
    {SYS_getcwd, "getcwd", -1},      {SYS_getdents64, "getdents64", -1},
};

static const struct sysdesc *lookup(long nr) {
    for (size_t i = 0; i < sizeof(TRACED) / sizeof(TRACED[0]); i++)
        if (TRACED[i].nr == nr) return &TRACED[i];
    return NULL;
}

static void read_string(pid_t pid, unsigned long addr, char *buf, size_t cap) {
    size_t n = 0;
    while (n + sizeof(long) < cap) {
        errno = 0;
        long word = ptrace(PTRACE_PEEKDATA, pid, addr + n, NULL);
        if (errno) break;
        memcpy(buf + n, &word, sizeof(long));
        if (memchr(&word, 0, sizeof(long))) return;
        n += sizeof(long);
    }
    buf[n] = '\0';
}

static unsigned long arg(const struct user_regs_struct *r, int i) {
    const unsigned long a[] = {r->rdi, r->rsi, r->rdx, r->r10, r->r8, r->r9};
    return a[i];
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s CMD [ARGS...]\n", argv[0]); return 2; }
    pid_t pid = fork();
    if (pid == 0) {
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        raise(SIGSTOP);
        execvp(argv[1], argv + 1);
        _exit(127);
    }
    int status;
    waitpid(pid, &status, 0);
    ptrace(PTRACE_SETOPTIONS, pid, NULL, PTRACE_O_TRACESYSGOOD | PTRACE_O_EXITKILL);
    int in_call = 0;
    char path[4096] = "";
    const struct sysdesc *cur = NULL;
    for (;;) {
        ptrace(PTRACE_SYSCALL, pid, NULL, NULL);
        if (waitpid(pid, &status, 0) < 0 || WIFEXITED(status) || WIFSIGNALED(status)) break;
        if (!(WIFSTOPPED(status) && WSTOPSIG(status) == (SIGTRAP | 0x80))) continue;
        struct user_regs_struct regs;
        ptrace(PTRACE_GETREGS, pid, NULL, &regs);
        if (!in_call) {
            cur = lookup((long)regs.orig_rax);
            path[0] = '\0';
            if (cur && cur->path_arg >= 0) read_string(pid, arg(&regs, cur->path_arg), path, sizeof path);
        } else if (cur) {
            fprintf(stderr, "[ftrace] %s(%s) = %lld\n", cur->name, path, (long long)regs.rax);
        }
        in_call = !in_call;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
