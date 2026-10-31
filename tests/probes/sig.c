/* The launcher's signature functions without Python, for tests/e2e/test_signatures.py:
 *
 *   sig [-i INIT] sha256 FILE                            the file's SHA-256, from another position
 *   sig [-i INIT] verify PUB STATEMENT SIGNATURE         fsb_sig_verify's result: 1, 0 or -1
 *   sig [-i INIT] half VECTORS                           fsb_sig_half's result per line of VECTORS:
 *                                                        HALF PEM MESSAGE SIGNATURE in hexadecimal,
 *                                                        "-" for empty
 *   sig [-i INIT] keynew PASSPHRASE KEY PUB              writes both files; the status
 *   sig [-i INIT] sign KEY PASSPHRASE STATEMENT SIG PUB  writes SIG and PUB; the status
 *   sig [-i INIT] queue KEY PASSPHRASE PUB STATEMENT SIG the error queue around each call
 *
 * Arguments are files, a passphrase is the bytes given. INIT is what the process does with OpenSSL
 * first, as CPython's _hashlib and _ssl may have done before a check runs: none, crypto
 * (OPENSSL_init_crypto), ssl (SSL_CTX_new) or digests (EVP_MD_do_all_provided on the default
 * context). A failed setup exits 3, a bad queue 4; nothing here frees less than it allocates, so
 * LeakSanitizer sees sig.c's leaks only. Built with -Wl,--wrap=EVP_DigestVerify: with
 * $PROBE_VERIFY_RESULT set, each verify returns that, which no input can make OpenSSL do.
 */
#include "integrity.h"
#include "sig.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* As sig.c: the distribution ships no OpenSSL headers. */
int OPENSSL_init_crypto(uint64_t, const void *);
typedef struct ssl_ctx_st SSL_CTX;
typedef struct ssl_method_st SSL_METHOD;
const SSL_METHOD *TLS_client_method(void);
SSL_CTX *SSL_CTX_new(const SSL_METHOD *);
void SSL_CTX_free(SSL_CTX *);
void EVP_MD_do_all_provided(void *, void (*)(void *, void *), void *);
void ERR_new(void);
void ERR_set_debug(const char *, int, const char *);
void ERR_set_error(int, int, const char *, ...);
unsigned long ERR_get_error(void);
unsigned long ERR_peek_error(void);
#define ERR_LIB_USER 128
#define OWN_REASON 7

int __real_EVP_DigestVerify(void *, const unsigned char *, size_t, const unsigned char *, size_t);

int __wrap_EVP_DigestVerify(void *context, const unsigned char *signature, size_t signature_size,
                            const unsigned char *message, size_t message_size) {
    int result = __real_EVP_DigestVerify(context, signature, signature_size, message, message_size);
    const char *forced = getenv("PROBE_VERIFY_RESULT");
    return forced != NULL ? atoi(forced) : result;
}

#define MAX_FILE (4u << 20)
#define SETUP_FAILED 3
#define BAD_QUEUE 4

struct bytes {
    uint8_t *data;
    size_t size;
};

static struct bytes slurp(const char *path) {
    struct bytes read_in = {malloc(MAX_FILE), 0};
    int fd = open(path, O_RDONLY);
    if (read_in.data == NULL || fd < 0) exit(SETUP_FAILED);
    for (ssize_t got; (got = read(fd, read_in.data + read_in.size, MAX_FILE - read_in.size)) > 0;)
        read_in.size += (size_t)got;
    close(fd);
    return read_in;
}

static void spit(const char *path, const uint8_t *data, size_t size) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0 || write(fd, data, size) != (ssize_t)size) exit(SETUP_FAILED);
    close(fd);
}

static void no_digest(void *digest, void *argument) {
    (void)digest;
    (void)argument;
}

static void pre_init(const char *what) {
    if (strcmp(what, "crypto") == 0) {
        if (OPENSSL_init_crypto(0, NULL) != 1) exit(SETUP_FAILED);
    } else if (strcmp(what, "ssl") == 0) {
        SSL_CTX *context = SSL_CTX_new(TLS_client_method());
        if (context == NULL) (void)ERR_get_error(); /* a config may refuse it: the point */
        SSL_CTX_free(context);
    } else if (strcmp(what, "digests") == 0) {
        EVP_MD_do_all_provided(NULL, no_digest, NULL);
    } else if (strcmp(what, "none") != 0) {
        exit(SETUP_FAILED);
    }
    while (ERR_get_error() != 0) {} /* a failed init's errors are the config's, not the checks' */
}

static int sha256(const char *path) {
    int fd = open(path, O_RDONLY);
    uint8_t digest[FSB_DIGEST_SIZE];
    if (fd < 0 || lseek(fd, 1, SEEK_SET) < 0 || fsb_sha256_file(fd, digest) != 0) return SETUP_FAILED;
    if (lseek(fd, 0, SEEK_CUR) != 1) return SETUP_FAILED; /* the position stays */
    close(fd);
    struct bytes whole = slurp(path);
    uint8_t again[FSB_DIGEST_SIZE];
    if (fsb_sha256_bytes(whole.data, whole.size, again) != 0 ||
        memcmp(digest, again, sizeof digest) != 0)
        return SETUP_FAILED;
    free(whole.data);
    for (unsigned i = 0; i < FSB_DIGEST_SIZE; i++) printf("%02x", digest[i]);
    printf("\n");
    return 0;
}

static int verify(const char *pub_path, const char *statement_path, const char *signature_path) {
    struct bytes pub = slurp(pub_path), statement = slurp(statement_path);
    struct bytes signature = slurp(signature_path);
    int result = fsb_sig_verify(pub.data, pub.size, statement.data, statement.size, signature.data,
                                signature.size);
    printf("%d\n", result);
    free(pub.data);
    free(statement.data);
    free(signature.data);
    return 0;
}

static struct bytes unhex(const char *text) {
    size_t digits = strcmp(text, "-") == 0 ? 0 : strlen(text);
    struct bytes out = {malloc(digits / 2 + 1), digits / 2};
    if (out.data == NULL) exit(SETUP_FAILED);
    for (size_t i = 0; i < out.size; i++) {
        unsigned byte;
        if (sscanf(text + 2 * i, "%2x", &byte) != 1) exit(SETUP_FAILED);
        out.data[i] = (uint8_t)byte;
    }
    return out;
}

static int halves(const char *path) {
    FILE *vectors = fopen(path, "r");
    if (vectors == NULL) return SETUP_FAILED;
    char *line = NULL;
    size_t capacity = 0;
    while (getline(&line, &capacity, vectors) > 0) {
        char *rest, *half = strtok_r(line, " \n", &rest);
        char *pem_hex = strtok_r(NULL, " \n", &rest), *message_hex = strtok_r(NULL, " \n", &rest);
        char *signature_hex = strtok_r(NULL, " \n", &rest);
        if (signature_hex == NULL) return SETUP_FAILED;
        struct bytes pem = unhex(pem_hex), message = unhex(message_hex);
        struct bytes signature = unhex(signature_hex);
        printf("%d\n", fsb_sig_half((unsigned)atoi(half), pem.data, pem.size, message.data,
                                    message.size, signature.data, signature.size));
        free(pem.data);
        free(message.data);
        free(signature.data);
    }
    free(line);
    fclose(vectors);
    return 0;
}

static int keynew(const char *passphrase, const char *key_path, const char *pub_path) {
    uint8_t *key = malloc(FSB_SIG_PEM_MAX), *pub = malloc(FSB_SIG_PEM_MAX);
    size_t key_size, pub_size;
    if (key == NULL || pub == NULL) return SETUP_FAILED;
    enum fsb_sig_status status = fsb_sig_key_new((const uint8_t *)passphrase, strlen(passphrase),
                                                 key, &key_size, pub, &pub_size);
    if (status == FSB_SIG_OK) {
        spit(key_path, key, key_size);
        spit(pub_path, pub, pub_size);
    }
    printf("%d\n", status);
    free(key);
    free(pub);
    return 0;
}

static int sign(const char *key_path, const char *passphrase, const char *statement_path,
                const char *signature_path, const char *pub_path) {
    struct bytes key = slurp(key_path), statement = slurp(statement_path);
    uint8_t signature[FSB_SIG_SIZE], *pub = malloc(FSB_SIG_PEM_MAX);
    size_t pub_size;
    if (pub == NULL) return SETUP_FAILED;
    enum fsb_sig_status status =
        fsb_sig_sign(key.data, key.size, (const uint8_t *)passphrase, strlen(passphrase),
                     statement.data, statement.size, signature, pub, &pub_size);
    if (status == FSB_SIG_OK) {
        spit(signature_path, signature, sizeof signature);
        spit(pub_path, pub, pub_size);
    }
    printf("%d\n", status);
    free(key.data);
    free(statement.data);
    free(pub);
    return 0;
}

/* Each call of the queue check, good and failing. */
struct call {
    struct bytes key, pub, statement, signature;
    const char *passphrase;
};

static void call_verify(const struct call *c) {
    (void)fsb_sig_verify(c->pub.data, c->pub.size, c->statement.data, c->statement.size,
                         c->signature.data, c->signature.size);
}

static void call_verify_bad_signature(const struct call *c) {
    uint8_t *flipped = malloc(c->signature.size);
    if (flipped == NULL) exit(SETUP_FAILED);
    memcpy(flipped, c->signature.data, c->signature.size);
    flipped[0] ^= 1;
    (void)fsb_sig_verify(c->pub.data, c->pub.size, c->statement.data, c->statement.size, flipped,
                         c->signature.size);
    free(flipped);
}

static void call_verify_bad_size(const struct call *c) {
    (void)fsb_sig_verify(c->pub.data, c->pub.size, c->statement.data, c->statement.size,
                         c->signature.data, c->signature.size - 1);
}

static void call_verify_bad_pub(const struct call *c) {
    (void)fsb_sig_verify(c->key.data, c->key.size, c->statement.data, c->statement.size,
                         c->signature.data, c->signature.size);
}

static void call_failed_then_good(const struct call *c) { /* a pin that fails, then one that passes */
    call_verify_bad_signature(c);
    call_verify(c);
}

static void call_half_bad(const struct call *c) {
    (void)fsb_sig_half(0, c->pub.data, c->pub.size, c->statement.data, c->statement.size,
                       c->signature.data, FSB_SIG_ED25519_SIZE);
}

static void call_sign(const struct call *c, const char *passphrase, const struct bytes *key) {
    uint8_t signature[FSB_SIG_SIZE], *pub = malloc(FSB_SIG_PEM_MAX);
    size_t pub_size;
    if (pub == NULL) exit(SETUP_FAILED);
    (void)fsb_sig_sign(key->data, key->size, (const uint8_t *)passphrase, strlen(passphrase),
                       c->statement.data, c->statement.size, signature, pub, &pub_size);
    free(pub);
}

static void call_sign_good(const struct call *c) {
    call_sign(c, c->passphrase, &c->key);
}

static void call_sign_wrong_passphrase(const struct call *c) {
    call_sign(c, "not the passphrase", &c->key);
}

static void call_sign_not_a_key(const struct call *c) {
    call_sign(c, c->passphrase, &c->pub);
}

static void call_key_new(const struct call *c) {
    uint8_t *key = malloc(FSB_SIG_PEM_MAX), *pub = malloc(FSB_SIG_PEM_MAX);
    size_t key_size, pub_size;
    if (key == NULL || pub == NULL) exit(SETUP_FAILED);
    (void)fsb_sig_key_new((const uint8_t *)c->passphrase, strlen(c->passphrase), key, &key_size, pub,
                          &pub_size);
    free(key);
    free(pub);
}

static void own_error(void) {
    ERR_new();
    ERR_set_debug("sig.c", 0, "own_error");
    ERR_set_error(ERR_LIB_USER, OWN_REASON, NULL);
}

static int queue(const char **paths, const char *passphrase) {
    void (*const calls[])(const struct call *) = {
        call_verify,      call_verify_bad_signature, call_verify_bad_size,
        call_verify_bad_pub, call_failed_then_good,  call_half_bad,
        call_sign_good,   call_sign_wrong_passphrase, call_sign_not_a_key,
        call_key_new,
    };
    struct call c = {slurp(paths[0]), slurp(paths[1]), slurp(paths[2]), slurp(paths[3]), passphrase};
    int failed = 0;
    for (size_t i = 0; i < sizeof calls / sizeof calls[0]; i++) {
        calls[i](&c);
        if (ERR_peek_error() != 0) {
            fprintf(stderr, "call %zu left an error in an empty queue\n", i);
            failed = BAD_QUEUE;
        }
        own_error();
        calls[i](&c);
        unsigned long kept = ERR_get_error();
        if (kept == 0 || (kept & 0x7fffff) != OWN_REASON || ERR_peek_error() != 0) {
            fprintf(stderr, "call %zu changed the error queued before it\n", i);
            failed = BAD_QUEUE;
        }
        while (ERR_get_error() != 0) {}
    }
    free(c.key.data);
    free(c.pub.data);
    free(c.statement.data);
    free(c.signature.data);
    return failed;
}

int main(int argc, char **argv) {
    if (argc > 2 && strcmp(argv[1], "-i") == 0) {
        pre_init(argv[2]);
        argc -= 2;
        argv += 2;
    }
    if (argc == 3 && strcmp(argv[1], "sha256") == 0) return sha256(argv[2]);
    if (argc == 5 && strcmp(argv[1], "verify") == 0) return verify(argv[2], argv[3], argv[4]);
    if (argc == 3 && strcmp(argv[1], "half") == 0) return halves(argv[2]);
    if (argc == 5 && strcmp(argv[1], "keynew") == 0) return keynew(argv[2], argv[3], argv[4]);
    if (argc == 7 && strcmp(argv[1], "sign") == 0)
        return sign(argv[2], argv[3], argv[4], argv[5], argv[6]);
    if (argc == 7 && strcmp(argv[1], "queue") == 0) {
        const char *paths[] = {argv[2], argv[4], argv[5], argv[6]};
        return queue(paths, argv[3]);
    }
    return SETUP_FAILED;
}
