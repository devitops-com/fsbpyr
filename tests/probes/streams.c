/* The launcher's blob streams on an executable image, for tests/e2e/test_stream_windows.py (D38):
 *
 *   streams FILE    for every file record, one line: its path, size and codec, the lengths of
 *                   the windows read from the start, then the SHA-256 of the content read from
 *                   the start, from its middle (a seek forward) and from its first byte on (a
 *                   seek back)
 *
 * Built with a small FSB_STREAM_WINDOW, so that blobs of a few MiB take several windows.
 */
#include "integrity.h"
#include "payload.h"

#include <stdio.h>
#include <stdlib.h>

int SHA256_Init(void *context);
int SHA256_Update(void *context, const void *data, size_t length);
int SHA256_Final(unsigned char *digest, void *context);

/* The SHA-256 of the content from ``start`` on, printed; false if the stream failed. */
static bool hashed_from(struct fsb_stream *s, uint64_t start, bool lengths) {
    fsb_sha256_context context;
    uint8_t digest[32];
    const uint8_t *data;
    uint64_t length;
    int status = fsb_stream_seek(s, start);
    SHA256_Init(&context);
    while (status == FSB_STREAM_DATA &&
           (status = fsb_stream_next(s, &data, &length)) == FSB_STREAM_DATA) {
        if (lengths) printf(" %llu", (unsigned long long)length);
        SHA256_Update(&context, data, length);
    }
    SHA256_Final(digest, &context);
    if (status != FSB_STREAM_END) return false;
    printf(" ");
    for (unsigned i = 0; i < sizeof digest; i++) printf("%02x", digest[i]);
    return true;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    FILE *file = fopen(argv[1], "rb");
    if (file == NULL || fseek(file, 0, SEEK_END) != 0) return 3;
    long size = ftell(file);
    uint8_t *image = malloc((size_t)size);
    if (image == NULL || fseek(file, 0, SEEK_SET) != 0 ||
        fread(image, 1, (size_t)size, file) != (size_t)size)
        return 3;
    fclose(file);
    static struct fsb_payload payload;
    const char *error = fsb_payload_parse(&payload, image, (uint64_t)size);
    if (error != NULL) {
        fprintf(stderr, "%s\n", error);
        return 1;
    }
    struct fsb_decoder *decoder = fsb_decoder_new(4);
    int failed = 0;
    for (uint32_t i = 0; i < fsb_count(&payload, FSB_SECTION_FILES); i++) {
        struct fsb_file f;
        struct fsb_blob b;
        const char *path;
        if (!fsb_get_file(&payload, i, &f) || !fsb_str(&payload, f.path, &path) ||
            !fsb_get_blob(&payload, f.blob, &b))
            return 1;
        struct fsb_stream *s = fsb_stream_open(&payload, decoder, &b);
        printf("%s %llu %u", path, (unsigned long long)b.size, (unsigned)b.codec);
        bool ok = s != NULL && hashed_from(s, 0, true) && (printf(" |"), true) &&
                  hashed_from(s, b.size / 2, false) && hashed_from(s, b.size > 0, false);
        printf("%s\n", ok ? "" : " FAILED");
        failed |= !ok;
        fsb_stream_close(s);
    }
    fsb_decoder_free(decoder);
    free(image);
    return failed;
}
