# Chat image normalization

Chat prepares a bounded image before expanding it to base64 or sending it to a
model. This also applies to PNG/JPEG files that the provider already accepts.

| Limit | Value |
| --- | --- |
| Source image | 32 MiB, 64 megapixels (includes 48 MP phone photos) |
| Model image | 3 MiB, longest edge 2560 px, at most 6 megapixels |
| Browser encoding | At most five attempts |

Small valid images retain their exact bytes. Larger images keep their aspect
ratio and are never cropped. PNG text/diagrams first try a resized lossless PNG;
when that is too large the browser uses WebP, preserving transparency. Photos
already in JPEG or converted from HEIC use JPEG. Oversized animated images use
their first frame. HEIC codec output is decoded and subjected to the same limits.
The browser uses the actual encoder output MIME and matching filename extension.

The existing administrator per-file and per-message limits still apply to the
prepared upload. Images can enter preparation up to the source safety limit even
when their original byte size exceeds the upload limit. Document limits are
unchanged. All composers serialize image preparation, check the final total
against the latest attachment list, and wait for preparation before sending.

The backend independently validates/normalizes uploaded and historical images.
Existing originals remain in the private attachment store; a versioned model
variant is cached beside an oversized original and deleted with that attachment.
Chat, historical replay, regeneration and GeoGebra use this same variant.
External image URLs are not fetched by this normalizer.

New private model history stores attachment references instead of inline base64.
The provider receives hydrated image bytes at request preparation time. Prior
images already present in retained history are not added a second time. Image
payloads do not enter the text tokenizer used for context/cache estimates; image
token budgets remain estimates, with provider usage authoritative.

The regression sample used during development was a 5712×4284 RGB PNG:
19,882,651 bytes became a 2560×1920 WebP of 318,414 bytes in Chromium (about 0.75 s).
The independent backend fallback produced 686,175 JPEG bytes (about 0.58 s).
Local persistence/replay tests verified identical model bytes across replay and
GeoGebra and a roughly 200-byte image-reference message instead of a 26 MB data
URL. The private sample is not included in the repository.

These limits reduce transport, serialization and image processing overhead.
They do not guarantee model response time or prevent upstream service failures.
