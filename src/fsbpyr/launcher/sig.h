/* Signatures (docs/signing.md §2, §4): the Ed25519 + ML-DSA-65 pair over a short statement, on the
 * OpenSSL the runtime links for _ssl and _hashlib.
 *
 * Python reads files, asks for passphrases and prints; these functions do the crypto. Each of
 * fsb_sig_key_new, fsb_sig_sign, fsb_sig_verify and fsb_sig_half makes a library context of its
 * own, loads no configuration into it and frees it, so openssl.cnf and the default context change
 * no result; each leaves the thread's error queue as it found it, since CPython's _ssl reads that
 * queue; and each takes its results from return values alone, so no OpenSSL text reaches Python.
 */
#ifndef FSBPYR_SIG_H
#define FSBPYR_SIG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FSB_SIG_ED25519_SIZE 64u
#define FSB_SIG_MLDSA65_SIZE 3309u
#define FSB_SIG_SIZE (FSB_SIG_ED25519_SIZE + FSB_SIG_MLDSA65_SIZE)
#define FSB_SIG_PASSPHRASE_MAX 1024u /* what OpenSSL's PEM reader takes */
#define FSB_SIG_PEM_MAX (16u << 10)  /* a .key has 6088 bytes, a .pub 2839 */

enum fsb_sig_status {
    FSB_SIG_OK = 0,
    FSB_SIG_BAD_KEY_FILE,   /* not two encrypted blocks with nothing around them */
    FSB_SIG_BAD_PASSPHRASE, /* empty, too long, or a block that does not open with it */
    FSB_SIG_BAD_KEY_TYPE,   /* opened, and is not Ed25519, then ML-DSA-65 */
    FSB_SIG_FAILED,         /* OpenSSL failed, or the new signature does not verify */
};

/* Both keys, as the .key and .pub bytes, each into a buffer of FSB_SIG_PEM_MAX bytes. */
enum fsb_sig_status fsb_sig_key_new(const uint8_t *passphrase, size_t passphrase_size, uint8_t *key,
                                    size_t *key_size, uint8_t *pub, size_t *pub_size);

/* The FSB_SIG_SIZE-byte signature of ``statement`` and the .pub of the key file ``key`` (into a
 * buffer of FSB_SIG_PEM_MAX bytes). The signature is checked against that .pub first. */
enum fsb_sig_status fsb_sig_sign(const uint8_t *key, size_t key_size, const uint8_t *passphrase,
                                 size_t passphrase_size, const uint8_t *statement,
                                 size_t statement_size, uint8_t signature[FSB_SIG_SIZE],
                                 uint8_t *pub, size_t *pub_size);

/* 1 if both halves of ``signature`` are good for the two keys of the .pub ``pub``; 0 if not, for
 * any size but FSB_SIG_SIZE too; -1 if ``pub`` is not a .pub (it is checked first). */
int fsb_sig_verify(const uint8_t *pub, size_t pub_size, const uint8_t *statement,
                   size_t statement_size, const uint8_t *signature, size_t signature_size);

/* The check fsb_sig_verify makes of one half (0 Ed25519, 1 ML-DSA-65), with ``pem`` one public key
 * of that algorithm; for the independent vectors, whose keys and messages come one at a time. */
int fsb_sig_half(unsigned half, const uint8_t *pem, size_t pem_size, const uint8_t *message,
                 size_t message_size, const uint8_t *signature, size_t signature_size);

#ifdef Py_PYTHON_H /* tests/probes/sig.c links these functions without Python */
extern PyMethodDef fsb_sig_methods[]; /* _sha256, _key_new, _sign and _verify of _fsbpyr */
#endif

#endif /* FSBPYR_SIG_H */
