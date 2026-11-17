"""Creates GitHub App installation tokens. In Azure the app's JWT is signed by
Key Vault, so the app's private key never leaves the vault. Local runs can sign
with the app's .pem file instead, since Key Vault has no local emulator."""

import base64
import hashlib
import json
import time

from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, utils

from github_api import GitHub


def base64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def key_vault_signer(key_id, credential):
    client = CryptographyClient(key_id, credential)
    return lambda digest: client.sign(SignatureAlgorithm.rs256, digest).signature


def local_key_signer(pem_path):
    with open(pem_path, "rb") as pem:
        private_key = serialization.load_pem_private_key(pem.read(), password=None)
    return lambda digest: private_key.sign(
        digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256())
    )


def create_app_jwt(client_id, sign):
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iat": now - 60, "exp": now + 540, "iss": client_id}
    signing_input = ".".join(
        base64url(json.dumps(part).encode()) for part in (header, claims)
    )
    digest = hashlib.sha256(signing_input.encode()).digest()
    return f"{signing_input}.{base64url(sign(digest))}"


def create_installation_token(repo, client_id, sign):
    app = GitHub(create_app_jwt(client_id, sign))
    installation = app.get(f"repos/{repo}/installation")
    access = app.post(
        f"app/installations/{installation['id']}/access_tokens",
        {
            "repositories": [repo.split("/")[1]],
            "permissions": {"contents": "write", "pull_requests": "write"},
        },
    )
    return access["token"]
