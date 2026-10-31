/* libFuzzer harness: the readers of a .pub and a .key (launcher/sig.c) on any bytes, under ASan and
 * UBSan, and with LeakSanitizer, since a refused file takes the paths that free the most. Built and
 * run by tests/e2e/test_fuzz.py, from a real .pub and a real .key under the passphrase "p", made
 * with one PBKDF2 iteration: at 600,000 a run would only ever derive keys.
 *
 * Whatever the input, the readers must refuse it or read it, never crash, and no input may make a
 * zeroed signature pass.
 */
#include "sig.h"

#include <stdlib.h>

static const uint8_t statement[] = "fsbpyr-signature 1\nsubject fuzz\nsha256 0\n";

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    static const uint8_t signature[FSB_SIG_SIZE];
    uint8_t made[FSB_SIG_SIZE], *pub = malloc(FSB_SIG_PEM_MAX);
    size_t pub_size;
    if (pub == NULL) return 0;
    if (fsb_sig_verify(data, size, statement, sizeof statement - 1, signature, sizeof signature) ==
            1 ||
        fsb_sig_half(0, data, size, statement, sizeof statement - 1, signature,
                     FSB_SIG_ED25519_SIZE) == 1 ||
        fsb_sig_half(1, data, size, statement, sizeof statement - 1, signature,
                     FSB_SIG_MLDSA65_SIZE) == 1)
        abort();
    (void)fsb_sig_sign(data, size, (const uint8_t *)"p", 1, statement, sizeof statement - 1, made,
                       pub, &pub_size);
    free(pub);
    return 0;
}
