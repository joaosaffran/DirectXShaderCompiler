import os

import azure.functions as func
from azure.identity import DefaultAzureCredential

from github_api import GitHub
from github_app import create_installation_token, key_vault_signer, local_key_signer
from submodule_updater import update_submodules

app = func.FunctionApp()


def app_signer():
    pem_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
    if pem_path:
        return local_key_signer(pem_path)
    return key_vault_signer(os.environ["GITHUB_APP_KEY_ID"], DefaultAzureCredential())


@app.timer_trigger(arg_name="timer", schedule="0 0 12 * * *")
def update_submodules_daily(timer: func.TimerRequest) -> None:
    repo = os.environ["TARGET_REPO"]
    token = create_installation_token(
        repo, os.environ["GITHUB_APP_CLIENT_ID"], app_signer()
    )
    github = GitHub(token)
    try:
        update_submodules(github, repo, os.environ.get("BASE_BRANCH", "main"))
    finally:
        github.delete("installation/token")
