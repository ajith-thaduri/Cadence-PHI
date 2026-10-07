"""Download OPEN-ACCESS public clinical corpora (network required; run once).

Only datasets whose licence permits download without a DUA are fetched:
  MEDDOCAN (Zenodo record 4279323, CC BY 4.0) - Spanish clinical case reports with synthetic PHI.
Restricted corpora (n2c2/i2b2, PhysioNet gold standard, SHIELD) are NEVER downloaded; supply
your own licensed copy with --data-path.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
import zipfile

from harness.paths import DATA

MEDDOCAN_URL = "https://zenodo.org/api/records/4279323/files/meddocan.zip/content"
MEDDOCAN_SHA256 = "d0e4708b58689bc1440ede6f89e017e58d667827d927827622d73810cd68eac3"


def main(argv=None) -> None:
    os.environ["OFFLINE_MODE"] = "false"
    out = DATA / "public" / "meddocan"
    out.mkdir(parents=True, exist_ok=True)
    z = out / "meddocan.zip"
    if not z.exists():
        print(f"downloading {MEDDOCAN_URL}")
        urllib.request.urlretrieve(MEDDOCAN_URL, z)
    digest = hashlib.sha256(z.read_bytes()).hexdigest()
    if digest != MEDDOCAN_SHA256:
        raise SystemExit(f"checksum mismatch: {digest} != {MEDDOCAN_SHA256}")
    with zipfile.ZipFile(z) as f:
        f.extractall(out)
    print(f"MEDDOCAN ready at {out / 'meddocan'} (sha256 verified, CC BY 4.0)")


if __name__ == "__main__":
    main()
