import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend


def generate_vapid_keys():
    # Generăm pereche de chei EC P-256 (standardul Web Push / VAPID)
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())

    # ── Cheia privată în format PEM pe o linie ──────────────────────────────
    private_pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Eliminăm header, footer și newline-uri → format compact pentru .env
    private_pem_str = private_pem_bytes.decode("utf-8")
    private_compact  = private_pem_str.strip()

    # ── Cheia publică în format base64url (Application Server Key) ──────────
    # Web Push specifică formatul: uncompressed EC point (0x04 + X + Y), base64url fără padding
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("utf-8")

    print("\n" + "═" * 60)
    print("  VAPID KEYS GENERATE CU SUCCES")
    print("═" * 60)
    print("\nAdaugă în .env și în Render Dashboard:\n")
    print(f"VAPID_PUBLIC_KEY={public_key_b64}")
    print()
    print("VAPID_PRIVATE_KEY=" + private_compact.replace("\n", "\\n"))
    print()
    print("VAPID_CLAIMS_SUB=mailto:contact@noianlab.ro")
    print()
    print("ADMIN_SECRET=genereaza_un_secret_random_32_caractere")
    print()
    print("─" * 60)
    print("⚠️  IMPORTANT: Salvează aceste chei în siguranță.")
    print("   Regenerarea lor va invalida toate subscriptions existente.")
    print("   Userii vor trebui să se re-subscribe.")
    print("═" * 60 + "\n")

    return {
        "vapid_public_key":  public_key_b64,
        "vapid_private_key": private_compact,
    }


if __name__ == "__main__":
    generate_vapid_keys()