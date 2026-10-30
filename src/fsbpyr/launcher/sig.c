/* Signatures (see sig.h). */
#ifndef FSB_SIG_NO_PYTHON
#include "launcher.h"
#endif

#include "sig.h"

#include "integrity.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

/* OpenSSL 3.5, as integrity.c declares its SHA-256: the distribution ships no headers. The suite
 * compiles this file with Debian's headers included as well, so a declaration that does not match
 * fails it (tests/e2e/test_signatures.py). */
typedef struct ossl_lib_ctx_st OSSL_LIB_CTX;
typedef struct evp_pkey_st EVP_PKEY;
typedef struct evp_pkey_ctx_st EVP_PKEY_CTX;
typedef struct evp_md_ctx_st EVP_MD_CTX;
typedef struct evp_cipher_st EVP_CIPHER;
typedef struct bio_st BIO;
typedef struct bio_method_st BIO_METHOD;
typedef struct pkcs8_priv_key_info_st PKCS8_PRIV_KEY_INFO;
typedef struct X509_sig_st X509_SIG;
typedef struct ossl_param_st OSSL_PARAM;
typedef int pem_password_cb(char *buf, int size, int rwflag, void *userdata);
#define BIO_CTRL_INFO 3 /* BIO_get_mem_data(bio, data), a macro, is BIO_ctrl(bio, 3, 0, data) */

OSSL_LIB_CTX *OSSL_LIB_CTX_new(void);
void OSSL_LIB_CTX_free(OSSL_LIB_CTX *);
int ERR_set_mark(void);
int ERR_pop_to_mark(void);
void OPENSSL_cleanse(void *, size_t);
const BIO_METHOD *BIO_s_mem(void);
BIO *BIO_new(const BIO_METHOD *);
BIO *BIO_new_mem_buf(const void *, int);
long BIO_ctrl(BIO *, int, long, void *);
int BIO_free(BIO *);
EVP_PKEY_CTX *EVP_PKEY_CTX_new_from_name(OSSL_LIB_CTX *, const char *, const char *);
int EVP_PKEY_keygen_init(EVP_PKEY_CTX *);
int EVP_PKEY_generate(EVP_PKEY_CTX *, EVP_PKEY **);
void EVP_PKEY_CTX_free(EVP_PKEY_CTX *);
void EVP_PKEY_free(EVP_PKEY *);
int EVP_PKEY_is_a(const EVP_PKEY *, const char *);
EVP_CIPHER *EVP_CIPHER_fetch(OSSL_LIB_CTX *, const char *, const char *);
void EVP_CIPHER_free(EVP_CIPHER *);
PKCS8_PRIV_KEY_INFO *EVP_PKEY2PKCS8(const EVP_PKEY *);
void PKCS8_PRIV_KEY_INFO_free(PKCS8_PRIV_KEY_INFO *);
X509_SIG *PKCS8_encrypt_ex(int, const EVP_CIPHER *, const char *, int, unsigned char *, int, int,
                           PKCS8_PRIV_KEY_INFO *, OSSL_LIB_CTX *, const char *);
void X509_SIG_free(X509_SIG *);
int PEM_write_bio_PKCS8(BIO *, const X509_SIG *);
int PEM_write_bio_PUBKEY_ex(BIO *, const EVP_PKEY *, OSSL_LIB_CTX *, const char *);
EVP_PKEY *PEM_read_bio_PUBKEY_ex(BIO *, EVP_PKEY **, pem_password_cb *, void *, OSSL_LIB_CTX *,
                                 const char *);
EVP_PKEY *PEM_read_bio_PrivateKey_ex(BIO *, EVP_PKEY **, pem_password_cb *, void *, OSSL_LIB_CTX *,
                                     const char *);
EVP_MD_CTX *EVP_MD_CTX_new(void);
void EVP_MD_CTX_free(EVP_MD_CTX *);
int EVP_DigestSignInit_ex(EVP_MD_CTX *, EVP_PKEY_CTX **, const char *, OSSL_LIB_CTX *, const char *,
                          EVP_PKEY *, const OSSL_PARAM *);
int EVP_DigestSign(EVP_MD_CTX *, unsigned char *, size_t *, const unsigned char *, size_t);
int EVP_DigestVerifyInit_ex(EVP_MD_CTX *, EVP_PKEY_CTX **, const char *, OSSL_LIB_CTX *,
                            const char *, EVP_PKEY *, const OSSL_PARAM *);
int EVP_DigestVerify(EVP_MD_CTX *, const unsigned char *, size_t, const unsigned char *, size_t);

#define PBKDF2_ITERATIONS 600000 /* as release-signing.sh's openssl pkcs8 -iter */
#define BEGIN_KEY "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
#define END_KEY "\n-----END ENCRYPTED PRIVATE KEY-----"
#define HALVES 2

static const char *const ALGORITHMS[HALVES] = {"ED25519", "ML-DSA-65"};
static const size_t HALF_SIZES[HALVES] = {FSB_SIG_ED25519_SIZE, FSB_SIG_MLDSA65_SIZE};

/* Each function below between ERR_set_mark() and ERR_pop_to_mark() leaves the error queue as it
 * was: on an empty queue the first sets no mark and returns 0, and the second then empties it. The
 * queue has 15 places: errors pushed past them are lost, as they are to any other OpenSSL call. */

/* The base64 lines of a PEM body: the alphabet, its padding and LFs. */
static bool base64_lines(const uint8_t *body, size_t size) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n";
    for (size_t i = 0; i < size; i++)
        if (body[i] == '\0' || strchr(alphabet, body[i]) == NULL) return false;
    return true;
}

/* The two blocks of a .key (docs/signing.md §2.3): an encrypted label and base64 lines, with
 * nothing before or between them and only LFs after them; the last LF may be missing, as a key
 * copied through a secret may lose it. OpenSSL's reader would take text around a block, a block
 * that is not encrypted (and ignore the passphrase), and anything that follows. That what the label
 * says is true only the reader can tell: see sign_half. */
static bool key_blocks(const uint8_t *pem, size_t size, const uint8_t *blocks[HALVES],
                       size_t sizes[HALVES]) {
    const size_t begin = sizeof BEGIN_KEY - 1, end = sizeof END_KEY - 1;
    if (size == 0 || size > FSB_SIG_PEM_MAX) return false;
    size_t at = 0;
    for (int i = 0; i < HALVES; i++) {
        if (size - at < begin || memcmp(pem + at, BEGIN_KEY, begin) != 0) return false;
        const uint8_t *ended = memmem(pem + at + begin, size - at - begin, END_KEY, end);
        if (ended == NULL) return false;
        size_t stop = (size_t)(ended - pem) + end;
        if (!base64_lines(pem + at + begin, (size_t)(ended - pem) - at - begin)) return false;
        if (stop < size && pem[stop] != '\n') return false;
        blocks[i] = pem + at;
        sizes[i] = stop - at;
        at = stop + (stop < size);
    }
    for (; at < size; at++)
        if (pem[at] != '\n') return false;
    return true;
}

struct passphrase {
    const uint8_t *bytes;
    size_t size;
    bool asked; /* the block being read did ask for it */
};

/* Given as a callback, never as NULL: without one OpenSSL asks on the terminal itself. */
static int give_passphrase(char *buffer, int size, int rwflag, void *userdata) {
    (void)rwflag;
    struct passphrase *given = userdata;
    given->asked = true;
    if (given->size > (size_t)size) return -1;
    memcpy(buffer, given->bytes, given->size);
    return (int)given->size;
}

/* For a .pub, which needs none. Its parser runs before the canonical check can refuse a file, and
 * a block with legacy encryption headers would have OpenSSL ask on the terminal, and read standard
 * input without one. */
static int no_passphrase(char *buffer, int size, int rwflag, void *userdata) {
    (void)buffer;
    (void)size;
    (void)rwflag;
    (void)userdata;
    return -1;
}

/* A public key of ``pem`` as ``half``'s algorithm, read from ``in``, written again to ``out``;
 * NULL if it is another, or none. */
static EVP_PKEY *public_key(OSSL_LIB_CTX *library, BIO *in, BIO *out, unsigned half) {
    EVP_PKEY *key = PEM_read_bio_PUBKEY_ex(in, NULL, no_passphrase, NULL, library, NULL);
    if (key != NULL && EVP_PKEY_is_a(key, ALGORITHMS[half]) == 1 &&
        PEM_write_bio_PUBKEY_ex(out, key, library, NULL) == 1)
        return key;
    EVP_PKEY_free(key);
    return NULL;
}

/* Whether ``out`` holds exactly the ``size`` bytes at ``pem``. */
static bool same_bytes(BIO *out, const uint8_t *pem, size_t size) {
    char *written = NULL;
    long length = BIO_ctrl(out, BIO_CTRL_INFO, 0, &written);
    return length >= 0 && (size_t)length == size && memcmp(written, pem, size) == 0;
}

/* The keys of the canonical file of ``first``..``last`` halves: byte for byte what OpenSSL writes
 * for them. OpenSSL's reader alone takes text around a key, CRLF, a third key and bytes after a
 * key inside a block. */
static bool public_keys(OSSL_LIB_CTX *library, const uint8_t *pem, size_t size, unsigned first,
                        unsigned last, EVP_PKEY *keys[HALVES]) {
    if (size == 0 || size > FSB_SIG_PEM_MAX) return false;
    BIO *in = BIO_new_mem_buf(pem, (int)size), *out = BIO_new(BIO_s_mem());
    bool ok = in != NULL && out != NULL;
    for (unsigned half = first; ok && half <= last; half++)
        ok = (keys[half] = public_key(library, in, out, half)) != NULL;
    ok = ok && same_bytes(out, pem, size);
    BIO_free(in);
    BIO_free(out);
    return ok;
}

/* OpenSSL's verify returns 1 for a good signature, 0 for a bad one and a negative value for an
 * error: only 1 passes. */
static bool half_good(OSSL_LIB_CTX *library, EVP_PKEY *key, const uint8_t *signature,
                      size_t signature_size, const uint8_t *message, size_t message_size) {
    EVP_MD_CTX *verifying = EVP_MD_CTX_new();
    bool good = verifying != NULL &&
                EVP_DigestVerifyInit_ex(verifying, NULL, NULL, library, NULL, key, NULL) == 1 &&
                EVP_DigestVerify(verifying, signature, signature_size, message, message_size) == 1;
    EVP_MD_CTX_free(verifying);
    return good;
}

static bool both_good(OSSL_LIB_CTX *library, EVP_PKEY *keys[HALVES], const uint8_t *statement,
                      size_t statement_size, const uint8_t *signature) {
    return half_good(library, keys[0], signature, HALF_SIZES[0], statement, statement_size) &&
           half_good(library, keys[1], signature + HALF_SIZES[0], HALF_SIZES[1], statement,
                     statement_size);
}

int fsb_sig_verify(const uint8_t *pub, size_t pub_size, const uint8_t *statement,
                   size_t statement_size, const uint8_t *signature, size_t signature_size) {
    (void)ERR_set_mark();
    OSSL_LIB_CTX *library = OSSL_LIB_CTX_new();
    EVP_PKEY *keys[HALVES] = {NULL, NULL};
    int result = -1;
    if (library != NULL && public_keys(library, pub, pub_size, 0, HALVES - 1, keys))
        result = signature_size == FSB_SIG_SIZE &&
                 both_good(library, keys, statement, statement_size, signature);
    EVP_PKEY_free(keys[0]);
    EVP_PKEY_free(keys[1]);
    OSSL_LIB_CTX_free(library);
    ERR_pop_to_mark();
    return result;
}

int fsb_sig_half(unsigned half, const uint8_t *pem, size_t pem_size, const uint8_t *message,
                 size_t message_size, const uint8_t *signature, size_t signature_size) {
    if (half >= HALVES) return -1;
    (void)ERR_set_mark();
    OSSL_LIB_CTX *library = OSSL_LIB_CTX_new();
    EVP_PKEY *keys[HALVES] = {NULL, NULL};
    int result = -1;
    if (library != NULL && public_keys(library, pem, pem_size, half, half, keys))
        result = half_good(library, keys[half], signature, signature_size, message, message_size);
    EVP_PKEY_free(keys[half]);
    OSSL_LIB_CTX_free(library);
    ERR_pop_to_mark();
    return result;
}

/* What a memory BIO holds, into a buffer of FSB_SIG_PEM_MAX bytes after ``*size`` bytes. */
static bool take(BIO *bio, uint8_t *into, size_t *size) {
    char *data = NULL;
    long length = BIO_ctrl(bio, BIO_CTRL_INFO, 0, &data);
    if (length < 0 || (size_t)length > FSB_SIG_PEM_MAX - *size) return false;
    memcpy(into + *size, data, (size_t)length);
    *size += (size_t)length;
    return true;
}

static bool valid_passphrase(size_t size) {
    return size > 0 && size <= FSB_SIG_PASSPHRASE_MAX;
}

/* One key of a new pair, appended to both files, its PKCS#8 block encrypted as the script's
 * `openssl pkcs8 -topk8 -v2 aes-256-cbc -v2prf hmacWithSHA256 -iter 600000`: pbe_nid -1 is PBES2
 * with the cipher, whose PRF for AES is hmacWithSHA256, and no salt asks for a random one. */
static bool new_key(OSSL_LIB_CTX *library, const EVP_CIPHER *cipher, unsigned half,
                    const uint8_t *passphrase, size_t passphrase_size, uint8_t *key,
                    size_t *key_size, uint8_t *pub, size_t *pub_size) {
    EVP_PKEY_CTX *generating = EVP_PKEY_CTX_new_from_name(library, ALGORITHMS[half], NULL);
    EVP_PKEY *pair = NULL;
    PKCS8_PRIV_KEY_INFO *info = NULL;
    X509_SIG *encrypted = NULL;
    BIO *key_pem = BIO_new(BIO_s_mem()), *pub_pem = BIO_new(BIO_s_mem());
    bool ok = generating != NULL && key_pem != NULL && pub_pem != NULL &&
              EVP_PKEY_keygen_init(generating) == 1 && EVP_PKEY_generate(generating, &pair) == 1 &&
              (info = EVP_PKEY2PKCS8(pair)) != NULL &&
              (encrypted = PKCS8_encrypt_ex(-1, cipher, (const char *)passphrase,
                                            (int)passphrase_size, NULL, 0, PBKDF2_ITERATIONS, info,
                                            library, NULL)) != NULL &&
              PEM_write_bio_PKCS8(key_pem, encrypted) == 1 &&
              PEM_write_bio_PUBKEY_ex(pub_pem, pair, library, NULL) == 1 &&
              take(key_pem, key, key_size) && take(pub_pem, pub, pub_size);
    BIO_free(key_pem);
    BIO_free(pub_pem);
    X509_SIG_free(encrypted);
    PKCS8_PRIV_KEY_INFO_free(info);
    EVP_PKEY_free(pair);
    EVP_PKEY_CTX_free(generating);
    return ok;
}

enum fsb_sig_status fsb_sig_key_new(const uint8_t *passphrase, size_t passphrase_size, uint8_t *key,
                                    size_t *key_size, uint8_t *pub, size_t *pub_size) {
    *key_size = *pub_size = 0;
    if (!valid_passphrase(passphrase_size)) return FSB_SIG_BAD_PASSPHRASE;
    (void)ERR_set_mark();
    OSSL_LIB_CTX *library = OSSL_LIB_CTX_new();
    EVP_CIPHER *cipher =
        library != NULL ? EVP_CIPHER_fetch(library, "AES-256-CBC", NULL) : NULL;
    bool ok = cipher != NULL;
    for (unsigned half = 0; ok && half < HALVES; half++)
        ok = new_key(library, cipher, half, passphrase, passphrase_size, key, key_size, pub,
                     pub_size);
    EVP_CIPHER_free(cipher);
    OSSL_LIB_CTX_free(library);
    ERR_pop_to_mark();
    return ok ? FSB_SIG_OK : FSB_SIG_FAILED;
}

/* One half of a new signature, and its public key written to ``pub_pem``. */
static enum fsb_sig_status sign_half(OSSL_LIB_CTX *library, unsigned half, const uint8_t *block,
                                     size_t block_size, struct passphrase *given,
                                     EVP_PKEY **pair, const uint8_t *statement,
                                     size_t statement_size, uint8_t *signature, BIO *pub_pem) {
    BIO *in = BIO_new_mem_buf(block, (int)block_size);
    if (in == NULL) return FSB_SIG_FAILED;
    given->asked = false;
    *pair = PEM_read_bio_PrivateKey_ex(in, NULL, give_passphrase, given, library, NULL);
    BIO_free(in);
    if (*pair == NULL) return FSB_SIG_BAD_PASSPHRASE;
    if (!given->asked) return FSB_SIG_BAD_KEY_FILE; /* a label of ENCRYPTED on a plain key */
    if (EVP_PKEY_is_a(*pair, ALGORITHMS[half]) != 1) return FSB_SIG_BAD_KEY_TYPE;
    EVP_MD_CTX *signing = EVP_MD_CTX_new();
    size_t size = HALF_SIZES[half];
    bool ok = signing != NULL &&
              EVP_DigestSignInit_ex(signing, NULL, NULL, library, NULL, *pair, NULL) == 1 &&
              EVP_DigestSign(signing, signature, &size, statement, statement_size) == 1 &&
              size == HALF_SIZES[half] &&
              PEM_write_bio_PUBKEY_ex(pub_pem, *pair, library, NULL) == 1;
    EVP_MD_CTX_free(signing);
    return ok ? FSB_SIG_OK : FSB_SIG_FAILED;
}

enum fsb_sig_status fsb_sig_sign(const uint8_t *key, size_t key_size, const uint8_t *passphrase,
                                 size_t passphrase_size, const uint8_t *statement,
                                 size_t statement_size, uint8_t signature[FSB_SIG_SIZE],
                                 uint8_t *pub, size_t *pub_size) {
    const uint8_t *blocks[HALVES];
    size_t sizes[HALVES];
    *pub_size = 0;
    if (!key_blocks(key, key_size, blocks, sizes)) return FSB_SIG_BAD_KEY_FILE;
    if (!valid_passphrase(passphrase_size)) return FSB_SIG_BAD_PASSPHRASE;
    (void)ERR_set_mark();
    struct passphrase given = {passphrase, passphrase_size, false};
    OSSL_LIB_CTX *library = OSSL_LIB_CTX_new();
    BIO *pub_pem = BIO_new(BIO_s_mem());
    EVP_PKEY *pairs[HALVES] = {NULL, NULL};
    enum fsb_sig_status status = library != NULL && pub_pem != NULL ? FSB_SIG_OK : FSB_SIG_FAILED;
    size_t offset = 0;
    for (unsigned half = 0; status == FSB_SIG_OK && half < HALVES; half++) {
        status = sign_half(library, half, blocks[half], sizes[half], &given, &pairs[half],
                           statement, statement_size, signature + offset, pub_pem);
        offset += HALF_SIZES[half];
    }
    /* Of what the file's own keys give, so a damaged key never yields a signature. */
    if (status == FSB_SIG_OK &&
        (!take(pub_pem, pub, pub_size) ||
         !both_good(library, pairs, statement, statement_size, signature)))
        status = FSB_SIG_FAILED;
    EVP_PKEY_free(pairs[0]);
    EVP_PKEY_free(pairs[1]);
    BIO_free(pub_pem);
    OSSL_LIB_CTX_free(library);
    ERR_pop_to_mark();
    if (status != FSB_SIG_OK) *pub_size = 0;
    return status;
}

#ifndef FSB_SIG_NO_PYTHON

/* _fsbpyr's functions. No key material is kept after a call; what Python holds is Python's. */

static PyObject *sig_sha256(PyObject *self, PyObject *arg) {
    (void)self;
    uint8_t digest[FSB_DIGEST_SIZE];
    int error;
    if (PyBytes_Check(arg)) {
        error = fsb_sha256_bytes((const uint8_t *)PyBytes_AS_STRING(arg),
                                 (size_t)PyBytes_GET_SIZE(arg), digest);
    } else {
        int fd = PyObject_AsFileDescriptor(arg);
        if (fd < 0) return NULL;
        Py_BEGIN_ALLOW_THREADS
        error = fsb_sha256_file(fd, digest);
        Py_END_ALLOW_THREADS
    }
    if (error != 0) {
        errno = error;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    return PyBytes_FromStringAndSize((const char *)digest, sizeof digest);
}

static PyObject *status_error(enum fsb_sig_status status) {
    static const char *const messages[] = {
        [FSB_SIG_BAD_KEY_FILE] = "not a key file",
        [FSB_SIG_BAD_PASSPHRASE] = "wrong passphrase?",
        [FSB_SIG_BAD_KEY_TYPE] = "it holds other keys than Ed25519, then ML-DSA-65",
        [FSB_SIG_FAILED] = "the signature could not be made",
    };
    return PyErr_Format(PyExc_ValueError, "%s", messages[status]);
}

static PyObject *sig_key_new(PyObject *self, PyObject *arg) {
    (void)self;
    if (!PyBytes_Check(arg)) return PyErr_Format(PyExc_TypeError, "expected bytes");
    uint8_t *key = malloc(FSB_SIG_PEM_MAX), *pub = malloc(FSB_SIG_PEM_MAX);
    size_t key_size = 0, pub_size = 0;
    if (key == NULL || pub == NULL) {
        free(key);
        free(pub);
        return PyErr_NoMemory();
    }
    enum fsb_sig_status status =
        fsb_sig_key_new((const uint8_t *)PyBytes_AS_STRING(arg), (size_t)PyBytes_GET_SIZE(arg), key,
                        &key_size, pub, &pub_size);
    PyObject *result =
        status != FSB_SIG_OK
            ? status_error(status)
            : Py_BuildValue("(y#y#)", key, (Py_ssize_t)key_size, pub, (Py_ssize_t)pub_size);
    OPENSSL_cleanse(key, FSB_SIG_PEM_MAX);
    free(key);
    free(pub);
    return result;
}

static PyObject *sig_sign(PyObject *self, PyObject *args) {
    (void)self;
    const char *key, *passphrase, *statement;
    Py_ssize_t key_size, passphrase_size, statement_size;
    if (!PyArg_ParseTuple(args, "y#y#y#", &key, &key_size, &passphrase, &passphrase_size,
                          &statement, &statement_size))
        return NULL;
    uint8_t signature[FSB_SIG_SIZE], *pub = malloc(FSB_SIG_PEM_MAX);
    size_t pub_size = 0;
    if (pub == NULL) return PyErr_NoMemory();
    enum fsb_sig_status status = fsb_sig_sign(
        (const uint8_t *)key, (size_t)key_size, (const uint8_t *)passphrase,
        (size_t)passphrase_size, (const uint8_t *)statement, (size_t)statement_size, signature, pub,
        &pub_size);
    PyObject *result =
        status != FSB_SIG_OK
            ? status_error(status)
            : Py_BuildValue("(y#y#)", signature, (Py_ssize_t)sizeof signature, pub,
                            (Py_ssize_t)pub_size);
    free(pub);
    return result;
}

static PyObject *sig_verify(PyObject *self, PyObject *args) {
    (void)self;
    const char *pub, *statement, *signature;
    Py_ssize_t pub_size, statement_size, signature_size;
    if (!PyArg_ParseTuple(args, "y#y#y#", &pub, &pub_size, &statement, &statement_size, &signature,
                          &signature_size))
        return NULL;
    int verified = fsb_sig_verify((const uint8_t *)pub, (size_t)pub_size,
                                  (const uint8_t *)statement, (size_t)statement_size,
                                  (const uint8_t *)signature, (size_t)signature_size);
    if (verified < 0) return PyErr_Format(PyExc_ValueError, "not a public key file");
    return PyBool_FromLong(verified == 1);
}

PyMethodDef fsb_sig_methods[] = {
    {"_sha256", sig_sha256, METH_O, "The SHA-256 of a file descriptor's file, or of bytes."},
    {"_key_new", sig_key_new, METH_O, "(.key, .pub) of a new pair, under a passphrase."},
    {"_sign", sig_sign, METH_VARARGS, "(signature, .pub) of a statement, with a .key."},
    {"_verify", sig_verify, METH_VARARGS, "Whether both halves of a signature are good."},
    {NULL, NULL, 0, NULL},
};

#endif /* FSB_SIG_NO_PYTHON */
