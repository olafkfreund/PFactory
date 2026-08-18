#!/usr/bin/env bash
# Assert a signed image's signature is where the CLUSTER looks for it.
#
# The "cosign verify" self-test in our signing workflows proves cosign can read
# what cosign just wrote. It cannot prove Kyverno can, because it runs the same
# cosign that produced the signature -- it agrees with itself by construction.
#
# That blind spot cost 3 days of stale deploys (Factory#799). cosign v3 moved
# signatures from the legacy `sha256-<digest>.sig` tag to an OCI 1.1 referrer.
# Both cosign versions verify their own output happily; Kyverno v1.18.2 reads
# the legacy tag ONLY, so every image built after the v3 bump was rejected at
# admission with "no signatures found" while every check stayed green.
#
# Measured 2026-08-18 against the two real images, and this script was checked
# to PASS the first and FAIL the second before being wired in:
#
#   sha-dbf8e6d  cosign v2  legacy .sig tag   -> Kyverno admits it
#   sha-e9655ce  cosign v3  OCI referrer      -> Kyverno rejects it
#
# Kyverno's `type: SigstoreBundle` does NOT rescue the v3 layout either: it was
# tested against the live cluster on a throwaway policy and still reported
# "no matching signatures found", because cosign v3 writes the OLD payload
# (artifactType https://sigstore.dev/cosign/sign/v1) at the NEW location, and
# that combination matches neither of Kyverno's two verifiers.
#
# Delete this script when Kyverno can verify whatever cosign then writes,
# proven against the cluster and not against cosign.
#
# Usage: assert-signature-location.sh <image-repo> <digest>
set -euo pipefail

image="${1:?usage: assert-signature-location.sh <image-repo> <sha256:digest>}"
digest="${2:?usage: assert-signature-location.sh <image-repo> <sha256:digest>}"

case "${digest}" in
  sha256:*) ;;
  *) echo "::error::expected a sha256: digest, got '${digest}'"; exit 1 ;;
esac

tree="$(cosign tree "${image}@${digest}" 2>&1)"
echo "${tree}"

expected="${digest/:/-}.sig"
if printf '%s' "${tree}" | grep -qF "${expected}"; then
  echo "OK: signature present at the legacy tag ${expected}"
  exit 0
fi

echo "::error::Signature for ${image}@${digest} is NOT at the legacy tag ${expected}."
echo "::error::Kyverno v1.18.2 reads that tag only and will reject this image at"
echo "::error::admission, however green the cosign self-test above is. cosign has"
echo "::error::most likely stored it as an OCI referrer instead."
echo "::error::See Factory#804 before changing the cosign-installer pin."
exit 1
