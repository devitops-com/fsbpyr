/* fsbpyr executable format v3 — C mirror of src/fsbpyr/payload/format.py (spec: docs/format.md).
 *
 * All integers are little-endian. Records are read with memcpy, never by casting pointers into
 * the mapping, so alignment of the mapped data never matters. tests/integration/
 * test_launcher_format.py compiles a probe against this header and compares every size, offset
 * and constant with the Python definitions.
 */
#ifndef FSBPYR_FORMAT_H
#define FSBPYR_FORMAT_H

#include <stddef.h>
#include <stdint.h>

#if !defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "fsbpyr format readers assume a little-endian host"
#endif

#define FSB_FORMAT_VERSION 3u
#define FSB_PAGE_SIZE 4096u
#define FSB_BLOB_ALIGN 16u
#define FSB_TABLE_ALIGN 8u
#define FSB_NONE 0xFFFFFFFFu
#define FSB_DIGEST_SIZE 32u
#define FSB_DIGEST_LEAF (UINT64_C(1) << 20) /* the integrity digest's piece size (D31) */
#define FSB_MAX_MODE 0777u
#define FSB_MAX_BLOB_SIZE (UINT64_C(1) << 40)
#define FSB_MAX_BLOCK_SIZE (UINT64_C(1) << 20) /* a block holds small blobs (D30) */

#define FSB_TRAILER_MAGIC "FSBPYR\0T"
#define FSB_HEADER_MAGIC "FSBPYR\0H"
#define FSB_MAGIC_SIZE 8u

enum fsb_section {
    FSB_SECTION_STRTAB = 0,
    FSB_SECTION_BLOBS = 1,
    FSB_SECTION_MODULES = 2,
    FSB_SECTION_FILES = 3,
    FSB_SECTION_DISTS = 4,
    FSB_SECTION_CLOSURES = 5,
    FSB_SECTION_DIGESTS = 6,
    FSB_SECTION_STARTUP = 7,
    FSB_SECTION_COUNT = 8,
};

/* BLOCK: inside a block; offset is the block's index, stored_size the position in it */
enum fsb_codec { FSB_CODEC_NONE = 0, FSB_CODEC_ZSTD = 1, FSB_CODEC_BLOCK = 2 };

enum fsb_module_kind {
    FSB_MODULE = 1,
    FSB_PACKAGE = 2,
    FSB_NAMESPACE = 3,
    FSB_EXTENSION = 4,
};

enum fsb_module_flag { FSB_MODULE_STARTUP = 1, FSB_MODULE_FS_REQUIRED_ROOT = 2 };

enum fsb_file_kind {
    FSB_FILE_RESOURCE = 1,
    FSB_FILE_SOURCE = 2,
    FSB_FILE_NATIVE_EXT = 3,
    FSB_FILE_NATIVE_LIB = 4,
    FSB_FILE_METADATA = 5,
};

enum fsb_file_flag { FSB_FILE_FS_REQUIRED = 1 };

struct fsb_str {
    uint32_t offset; /* into STRTAB; the string is followed by one NUL byte */
    uint32_t length; /* bytes, excluding the NUL */
};

struct fsb_trailer {
    char magic[8];
    uint32_t format_version;
    uint32_t flags;
    uint64_t payload_offset;
    uint64_t payload_size;
    uint8_t payload_sha256[32];
};

struct fsb_section_ref {
    uint64_t offset;
    uint64_t count;
};

struct fsb_header {
    char magic[8];
    uint32_t format_version;
    uint32_t header_size;
    uint32_t python_hexversion;
    uint8_t bytecode_magic[4];
    uint8_t generation_id[32];
    struct fsb_str app_name;
    struct fsb_str entrypoint;
    struct fsb_str build_info;
    struct fsb_section_ref sections[FSB_SECTION_COUNT];
};

struct fsb_blob {
    uint64_t offset;
    uint64_t stored_size;
    uint64_t size;
    uint8_t codec;
    uint8_t reserved8;
    uint16_t reserved16;
    uint32_t digest;
};

struct fsb_module {
    struct fsb_str name;
    struct fsb_str path;
    uint8_t kind;
    uint8_t flags;
    uint16_t reserved16;
    uint32_t code;
    uint32_t source;
    uint32_t file;
    uint32_t dist;
    uint32_t reserved32;
};

struct fsb_file {
    struct fsb_str path;
    uint8_t kind;
    uint8_t flags;
    uint16_t mode;
    uint32_t blob;
    uint32_t dist;
    uint32_t closure;
};

struct fsb_dist {
    struct fsb_str name;
    struct fsb_str dist_info;
    struct fsb_str version;
};

_Static_assert(sizeof(struct fsb_trailer) == 64, "trailer layout");
_Static_assert(sizeof(struct fsb_header) == 208, "header layout");
_Static_assert(sizeof(struct fsb_blob) == 32, "blob layout");
_Static_assert(sizeof(struct fsb_module) == 40, "module layout");
_Static_assert(sizeof(struct fsb_file) == 24, "file layout");
_Static_assert(sizeof(struct fsb_dist) == 24, "dist layout");

#endif /* FSBPYR_FORMAT_H */
