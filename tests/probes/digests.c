/* The launcher's digest routines on files, for tests/e2e/test_digests.py (D38):
 *
 *   digests batch FILE...       fsb_tree_digests of the files in one call: odd ones read whole
 *                               into memory, even ones hashed through their descriptors
 *   digests pieces SIZE FILE    fsb_tree_add of the file's content, SIZE bytes at a time
 *
 * One hexadecimal digest per line. Built with a small FSB_PASS_LEAVES, so that passes end inside
 * files and between them.
 */
#include "integrity.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void print(const uint8_t digest[FSB_DIGEST_SIZE]) {
    for (unsigned i = 0; i < FSB_DIGEST_SIZE; i++) printf("%02x", digest[i]);
    printf("\n");
}

static uint8_t *whole(int fd, uint64_t size) {
    uint8_t *data = malloc(size + 1);
    if (data == NULL || pread(fd, data, size, 0) != (ssize_t)size) exit(3);
    return data;
}

static int batch(int count, char **paths) {
    const uint8_t **data = calloc((size_t)count, sizeof *data);
    int *fds = calloc((size_t)count, sizeof *fds);
    uint64_t *lengths = calloc((size_t)count, sizeof *lengths);
    uint8_t (*digests)[FSB_DIGEST_SIZE] = calloc((size_t)count, sizeof *digests);
    if (data == NULL || fds == NULL || lengths == NULL || digests == NULL) return 3;
    for (int i = 0; i < count; i++) {
        struct stat st;
        if ((fds[i] = open(paths[i], O_RDONLY)) < 0 || fstat(fds[i], &st) != 0) return 3;
        lengths[i] = (uint64_t)st.st_size;
        data[i] = i % 2 ? whole(fds[i], lengths[i]) : NULL;
    }
    int status = fsb_tree_digests((uint64_t)count, data, fds, lengths, digests);
    for (int i = 0; status == 0 && i < count; i++) print(digests[i]);
    for (int i = 0; i < count; i++) {
        free((void *)data[i]);
        close(fds[i]);
    }
    free(data);
    free(fds);
    free(lengths);
    free(digests);
    return status != 0;
}

static int pieces(uint64_t size, const char *path) {
    struct stat st;
    int fd = open(path, O_RDONLY);
    if (fd < 0 || fstat(fd, &st) != 0 || size == 0) return 3;
    uint64_t length = (uint64_t)st.st_size;
    uint8_t *data = whole(fd, length), digest[FSB_DIGEST_SIZE];
    struct fsb_tree tree;
    int status = 0;
    fsb_tree_init(&tree);
    for (uint64_t at = 0; status == 0 && at < length; at += size)
        status = fsb_tree_add(&tree, data + at, length - at < size ? length - at : size);
    fsb_tree_final(&tree, digest);
    if (status == 0) print(digest);
    free(data);
    close(fd);
    return status != 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "batch") == 0) return batch(argc - 2, argv + 2);
    if (argc == 4 && strcmp(argv[1], "pieces") == 0)
        return pieces(strtoull(argv[2], NULL, 10), argv[3]);
    return 2;
}
