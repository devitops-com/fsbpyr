/* The instance limit (launcher/slots.c), for tests/e2e/test_slots.py (D45):
 *
 *   slots limit            prints fsb_instance_limit()
 *   slots hold INO DIR...  fsb_hold_slot for the executable (1, INO) with the directories DIR...;
 *                          prints "held" if it kept a descriptor (the slot), else "unguarded",
 *                          then waits for the end of its stdin
 *   slots fork INO DIR...  the same, then forks: the parent exits, the child waits instead
 *   slots exec INO DIR...  the same, then executes "slots hold INO DIR..." in its place
 *   slots low INO DIR...   with descriptors 0 and 2 closed first: prints the descriptors that
 *                          lead to the slot file, or nothing
 *
 * A start that finds every slot taken ends as fsb_hold_slot ends it (exit 70, or 0 in a
 * background collection), printing nothing on stdout.
 */
#include "slots.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* The lowest descriptor number free. */
static int lowest_free(void) {
    int fd = dup(0);
    if (fd >= 0) close(fd);
    return fd;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "limit") == 0) {
        printf("%u\n", fsb_instance_limit());
        return 0;
    }
    if (argc < 4) return 2;
    if (strcmp(argv[1], "low") == 0) {
        char name[32], link[64], target[PATH_MAX];
        close(0);
        close(2);
        fsb_hold_slot(1, strtoull(argv[2], NULL, 10), (const char *const *)argv + 3,
                      (size_t)argc - 3);
        snprintf(name, sizeof name, "/fsbpyr-%lu", (unsigned long)geteuid());
        for (int fd = 0; fd < 64; fd++) {
            snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
            ssize_t n = readlink(link, target, sizeof target - 1);
            if (n <= 0) continue;
            target[n] = '\0';
            size_t length = strlen(target), tail = strlen(name);
            if (length >= tail && strcmp(target + length - tail, name) == 0) printf("%d\n", fd);
        }
        return 0;
    }
    int before = lowest_free();
    fsb_hold_slot(1, strtoull(argv[2], NULL, 10), (const char *const *)argv + 3,
                  (size_t)argc - 3);
    printf("%s\n", lowest_free() != before ? "held" : "unguarded");
    fflush(stdout);
    if (strcmp(argv[1], "fork") == 0) {
        pid_t child = fork();
        if (child != 0) return child < 0;
    } else if (strcmp(argv[1], "exec") == 0) {
        argv[1] = "hold";
        execv("/proc/self/exe", argv);
        return 1;
    }
    while (getchar() != EOF) {}
    return 0;
}
